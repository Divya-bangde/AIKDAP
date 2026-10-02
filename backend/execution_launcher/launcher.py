"""Sprint 16 Phase 7B.25 -- the REAL Docker execution launcher.

THIS IS THE ONLY MODULE IN THIS APPLICATION PERMITTED TO IMPORT THE
DOCKER SDK. `docker_policy.py`/`translator.py`/`resolvers.py` stay
exactly as pure/I-O-free (policy) or DB-only (resolution) as their own
docstrings already declare -- this module is additive, not a rewrite of
any of them. Nothing here decides POLICY: it trusts `ApprovedLaunchSpec`
completely, translating its already-approved fields into the real
Docker API and driving the lifecycle the Sprint 16 Phase 7B.25 real
Docker spike observed directly (see that spike's notes for every claim
below marked OBSERVED).

Lifecycle (Docker call -> durable persist, in this exact order, each
persist step its own commit -- "durable identity before the next risky
operation," the same discipline every prior execution-module phase in
this sprint has used):

    ExecutionAttempt(PENDING_CREATE)          [already exists]
        -> docker create
        -> persist container_id + status=CREATED
        -> docker start
        -> persist status=RUNNING, started_at (job.status=RUNNING too,
           Sprint 16 Phase 7B.28)
        -> BOUNDED docker wait (`_wait_bounded`, Sprint 16 Phase 7B.28:
           polls in `_POLL_INTERVAL_S` slices, terminates the container
           if `approved.timeout_s` elapses or a durable cancellation
           request is observed -- see that function's own docstring)
        -> inspect (authoritative for Status/ExitCode/FinishedAt --
           OBSERVED: wait()'s own return is `{"StatusCode": N}` only)
        -> persist status=EXITED, exit_code, completed_at (job.status
           becomes exactly one of SUCCEEDED/FAILED/TIMED_OUT/CANCELLED)
        -> collect stdout/stderr (bounded)
        -> persist result via the existing Asset/StorageProvider
           mechanism (a new column on ExecutionAttempt was explicitly
           out of scope this phase; `error_message` keeps its existing,
           narrower meaning -- a short diagnostic -- rather than being
           repurposed to hold full output, which is exactly the kind of
           field-semantic conflation Phase 7B.24 spent a whole slice
           correcting for `input_asset_ids`)
        -> remove container (idempotent: NotFound == already clean,
           OBSERVED)

Docker never raises for a nonzero application exit code (OBSERVED,
spike Test 2) -- `succeeded` below is computed explicitly from
`exit_code == 0 and termination_reason == "natural"`, never inferred
from the absence of an exception.

Sprint 16 Phase 7B.28 closed the "no timeout" gap Phase 7B.26 flagged:
`docker wait()` is never called unbounded any more -- see
`_wait_bounded`'s docstring for the real-daemon evidence behind the
bounded-polling design and why a naive `asyncio.wait_for()` wrapper
would not actually have been safe. Docker-aware crash RECONCILIATION
(orphan discovery, startup scanning) remains out of scope -- Phase
7B.29's job.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import docker
import docker.errors
import requests.exceptions
import urllib3.exceptions
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging.logger import get_logger
from app.modules.assets.enums import AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import get_storage_provider
from app.modules.execution.enums import ExecutionAttemptStatus, ExecutionJobStatus
from app.modules.execution.repository import ExecutionAttemptRepository, ExecutionJobRepository
from execution_launcher.docker_policy import resource_class_timeout_s
from execution_launcher.models import ApprovedLaunchSpec, SecurityBlocked

logger = get_logger(__name__)

#: Docker's own log stream has no size cap; without one, a runaway
#: container could exhaust this process's memory reading its own
#: output. No such bound existed anywhere in this codebase before this
#: phase (checked: no MAX_OUTPUT/1 MiB constant anywhere in
#: execution_launcher/ or app/modules/execution/) -- this is a NEW
#: bound, not a pre-existing policy being "enforced".
MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MiB, per stdout and per stderr

#: `ExecutionAttempt.error_message` is `Text` (unbounded in Postgres),
#: but a diagnostic is meant to be read, not to hold megabytes --
#: bounded defensively, matching every other diagnostic constant in
#: this codebase being a short, fixed(-ish) string.
_DIAGNOSTIC_MAX_CHARS = 2000

_DOCKER_ZERO_TIMESTAMP = "0001-01-01T00:00:00Z"

#: Sprint 16 Phase 7B.28 -- how often the bounded wait loop below polls
#: for "has the deadline elapsed" / "has cancellation been requested".
#: A pure implementation/responsiveness knob, NOT policy -- unlike
#: `ApprovedLaunchSpec.timeout_s` (guard-derived, resource-class-
#: specific, audited), this value is fixed and identical for every
#: launch regardless of resource class, the same way `MAX_OUTPUT_BYTES`
#: above is a fixed implementation bound, not a per-class policy value.
_POLL_INTERVAL_S = 1.0

TerminationReason = Literal["natural", "timeout", "cancelled"]


@dataclass(frozen=True)
class ExecutionOutcome:
    """What `execute_approved_launch` durably accomplished. `succeeded`
    is computed explicitly from the exit code -- Docker itself never
    tells you this (OBSERVED)."""

    succeeded: bool
    exit_code: int | None
    result_asset_id: uuid.UUID | None
    diagnostic: str | None


@dataclass(frozen=True)
class ReconciliationOutcome:
    """What `reconcile_attempt` (Sprint 16 Phase 7B.29) determined and/or
    did for one attempt. `action` is a short, closed label for
    logging/tests -- see `reconcile_attempt`'s own docstring for the full
    list of values and what each means. Deliberately a SEPARATE type from
    `ExecutionOutcome`: reconciliation has more possible outcomes than a
    fresh launch does (deferred-by-daemon-failure, already-clean-no-op,
    inconsistent-state-preserved, ...), and conflating the two would force
    those extra states into `ExecutionOutcome.succeeded`'s two-valued
    shape, which cannot represent them honestly.
    """

    action: str
    attempt_status: ExecutionAttemptStatus | None
    job_status: ExecutionJobStatus | None
    result_asset_id: uuid.UUID | None


#: `ExecutionAttemptStatus`/`ExecutionJobStatus` values reconciliation
#: treats as "nothing left to determine" -- reused across every branch
#: below rather than repeating the tuple literal.
_TERMINAL_ATTEMPT_STATUSES = (ExecutionAttemptStatus.EXITED, ExecutionAttemptStatus.UNKNOWN)
_TERMINAL_JOB_STATUSES = (
    ExecutionJobStatus.SUCCEEDED,
    ExecutionJobStatus.FAILED,
    ExecutionJobStatus.TIMED_OUT,
    ExecutionJobStatus.CANCELLED,
)

#: Statuses `ExecutionJobRepository.finalize_running_job` accepts as a
#: source (Sprint 16 Phase 7B.29) -- `VALIDATING` is included alongside
#: `RUNNING`/`CANCEL_REQUESTED` because `execute_approved_launch` writes
#: `job.status = RUNNING` only AFTER `docker start()` succeeds, so a crash
#: between `docker create()` and `docker start()`, or between the
#: attempt's own `RUNNING` write and the job's, leaves the JOB at
#: `VALIDATING` while the ATTEMPT has already progressed further -- see
#: `finalize_running_job`'s own docstring for why this is safe. Every
#: branch below that decides "is this job genuinely in-flight" reuses this
#: SAME tuple as the atomic method it is about to call, so the two can
#: never silently drift apart.
_IN_FLIGHT_JOB_STATUSES = (
    ExecutionJobStatus.VALIDATING,
    ExecutionJobStatus.RUNNING,
    ExecutionJobStatus.CANCEL_REQUESTED,
)


def _parse_docker_timestamp(value: str | None) -> datetime | None:
    """Docker's RFC3339 timestamps carry up to 9 fractional digits
    (nanoseconds) and a bare `Z` -- both OBSERVED directly in the spike
    (e.g. `"2026-09-04T13:51:06.495614003Z"`), neither of which
    `datetime.fromisoformat` accepts as-is on every supported Python
    version. Truncated to microsecond precision (Postgres's own
    `DateTime` resolution) rather than rejected. The zero sentinel
    Docker uses for "not yet set" (`0001-01-01T00:00:00Z`, OBSERVED on
    a freshly created, not-yet-started container) maps to `None`, not a
    real timestamp.
    """
    if not value or value == _DOCKER_ZERO_TIMESTAMP:
        return None
    iso = value.replace("Z", "+00:00")
    if "." in iso:
        head, _, rest = iso.partition(".")
        frac, _, offset = rest.partition("+")
        iso = f"{head}.{frac[:6]}+{offset}"
    return datetime.fromisoformat(iso)


def _bounded_text(value: str, *, max_chars: int = _DIAGNOSTIC_MAX_CHARS) -> str:
    return value if len(value) <= max_chars else value[:max_chars] + "...(truncated)"


def _docker_create_kwargs(approved: ApprovedLaunchSpec, *, container_name: str) -> dict:
    """Pure translation, no Docker/network I/O -- independently testable.

    Only fields `ApprovedLaunchSpec` actually carries are set (Phase
    7B.25 scope: "do not add any field that isn't justified by the
    existing ApprovedLaunchSpec"). Fields the approved spec deliberately
    does NOT carry (cap_add, devices, pid_mode, ipc_mode, entrypoint --
    see that dataclass's own docstring: "their single legal value is
    implied by this type's existence, not stored redundantly") are left
    to Docker's own defaults, which already match the intended fixed
    value (no added capabilities, no devices, default pid/ipc
    namespaces, the image's own entrypoint).

    `tmpfs_mb` -> docker-py's `tmpfs={"/tmp": f"size={mb}m"}` dict shape
    is the exact translation the real spike identified as necessary
    (spike notes, "Security / Resource Invocation Observations").
    """
    return {
        "image": approved.image,
        "name": container_name,
        "command": list(approved.command),
        "network_mode": approved.network_mode,
        "privileged": approved.privileged,
        "cap_drop": list(approved.cap_drop),
        "security_opt": list(approved.security_opt),
        "read_only": approved.read_only,
        "user": approved.user,
        "mem_limit": f"{approved.mem_limit_mb}m",
        "memswap_limit": f"{approved.memswap_limit_mb}m",
        "nano_cpus": int(approved.cpus * 1_000_000_000),
        "pids_limit": approved.pids_limit,
        "tmpfs": {"/tmp": f"size={approved.tmpfs_mb}m"},
        "environment": dict(approved.environment),
        "volumes": {
            mount.source: {"bind": mount.destination, "mode": mount.mode}
            for mount in approved.mounts
        },
        "detach": True,
    }


async def _read_bounded_logs(container, *, stdout: bool, stderr: bool) -> tuple[str, bool]:
    """Streams (never a single unbounded `.logs()` call) up to
    `MAX_OUTPUT_BYTES`, then stops reading -- bounds THIS PROCESS's
    memory regardless of how much the container actually wrote.
    Returns (decoded text, was_truncated).
    """

    def _read() -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        for chunk in container.logs(stdout=stdout, stderr=stderr, stream=True, follow=False):
            total += len(chunk)
            if total > MAX_OUTPUT_BYTES:
                remaining = MAX_OUTPUT_BYTES - (total - len(chunk))
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                truncated = True
                break
            chunks.append(chunk)
        return b"".join(chunks), truncated

    raw, truncated = await asyncio.to_thread(_read)
    return raw.decode("utf-8", errors="replace"), truncated


async def _persist_result_asset(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    attempt_id: uuid.UUID,
    job_id: uuid.UUID,
    exit_code: int,
    stdout: str,
    stdout_truncated: bool,
    stderr: str,
    stderr_truncated: bool,
) -> uuid.UUID:
    """Persists captured output via the EXISTING artifact mechanism
    (`StorageProvider` + `AssetRepository`) -- the same two primitives
    `ExperimentPlanService._persist_new` already uses for a different
    kind of generated artifact (Sprint 16 Phase 6). No new subsystem.

    The link back to the attempt/job lives in `Asset.asset_metadata`
    (JSONB, already schema-free) rather than a new foreign-key column
    on `ExecutionAttempt` -- this phase's explicit scope limit is "no
    new schema" (no migration), matching exactly how Phase 7B.23/7B.24
    threaded new plan-level facts through existing JSONB rather than
    adding columns.
    """
    payload = {
        "execution_attempt_id": str(attempt_id),
        "execution_job_id": str(job_id),
        "exit_code": exit_code,
        "stdout": stdout,
        "stdout_truncated": stdout_truncated,
        "stderr": stderr,
        "stderr_truncated": stderr_truncated,
    }
    content = json.dumps(payload, indent=2).encode("utf-8")
    filename = f"execution-{attempt_id}.json"
    storage = get_storage_provider()
    storage_path = await storage.save(project_id=project_id, filename=filename, content=content)

    asset = Asset(
        project_id=project_id,
        owner_id=owner_id,
        title=f"Execution result {attempt_id}",
        description=f"stdout/stderr for execution attempt {attempt_id} (exit_code={exit_code})",
        asset_type=AssetType.WORKFLOW_OUTPUT,
        status=AssetStatus.ACTIVE,
        source=AssetSource.GENERATED,
        mime_type="application/json",
        file_name=filename,
        file_extension="json",
        file_size=len(content),
        storage_path=storage_path,
        checksum="",
        tags=["execution-result"],
        asset_metadata=payload,
    )
    created = await AssetRepository(session).create(asset)
    await session.commit()
    return created.id


async def _find_or_create_result_asset(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    attempt_id: uuid.UUID,
    job_id: uuid.UUID,
    exit_code: int,
    stdout: str,
    stdout_truncated: bool,
    stderr: str,
    stderr_truncated: bool,
) -> uuid.UUID:
    """The idempotent wrapper around `_persist_result_asset` (Sprint 16
    Phase 7B.29): checks `AssetRepository.find_by_execution_attempt`
    FIRST and reuses whatever it finds, never creating a second result
    `Asset` for the same attempt.

    Safe under the concurrency this phase requires (T9/T16: two
    reconciliation workers processing the same attempt) NOT because this
    check-then-create sequence is atomic by itself -- it is not, and a
    bare `SELECT` then `INSERT` would still race -- but because every
    caller of this function only ever reaches it after independently
    winning `ExecutionJobRepository.finalize_running_job`'s atomic
    conditional `UPDATE` (see `_finalize_exited_attempt`). Postgres
    guarantees exactly one concurrent `UPDATE ... WHERE status IN
    (...)` against the same row succeeds; every other racer's call to
    THAT method returns `None` and never reaches this function at all,
    which is what actually makes creation single-writer-per-attempt, not
    this function's own check.
    """
    existing = await AssetRepository(session).find_by_execution_attempt(attempt_id)
    if existing is not None:
        return existing.id
    return await _persist_result_asset(
        session,
        project_id=project_id,
        owner_id=owner_id,
        attempt_id=attempt_id,
        job_id=job_id,
        exit_code=exit_code,
        stdout=stdout,
        stdout_truncated=stdout_truncated,
        stderr=stderr,
        stderr_truncated=stderr_truncated,
    )


def _safe_resource_class_timeout(job) -> float:
    """Re-derives `job.resource_class`'s bounded-execution timeout via
    `docker_policy.resource_class_timeout_s` (Sprint 16 Phase 7B.29) --
    the same "guard re-derives, never trusts a persisted policy value"
    discipline this codebase already uses everywhere else, applied here
    because `ApprovedLaunchSpec.timeout_s` is never itself persisted.

    Falls back to the largest currently-configured resource-class
    ceiling (never raises) if `job.resource_class` is somehow not in the
    current catalog -- should not be reachable (the value was validated
    by the SAME catalog at launch time), and a reconciliation pass must
    degrade to "wait a little longer" rather than crash on an
    unrecognized value.
    """
    try:
        return resource_class_timeout_s(job.resource_class)
    except SecurityBlocked:
        return 10.0


def _is_client_poll_timeout(exc: BaseException) -> bool:
    """OBSERVED via a real probe (Sprint 16 Phase 7B.28, real daemon):
    docker-py's `container.wait(timeout=N)` raises `requests.exceptions
    .ConnectionError` whose `__context__` is a `urllib3.exceptions.
    ReadTimeoutError` when the client-side poll deadline elapses while
    the container is STILL RUNNING server-side (confirmed via a
    concurrent `container.reload()` showing `State.Running=True`
    immediately after). This is the ONLY shape this function treats as
    "still running, keep polling" -- any other `ConnectionError` (e.g.
    a genuinely unreachable daemon, OBSERVED separately to raise
    `docker.errors.DockerException` at client-construction time, not
    this) is a real problem and must propagate, never be silently
    retried."""
    return isinstance(exc, requests.exceptions.ConnectionError) and isinstance(
        exc.__context__, urllib3.exceptions.ReadTimeoutError
    )


def _terminate_container(container) -> None:
    """`kill()` -- immediate SIGKILL, no grace period (OBSERVED, real
    probe: returns in <50ms). Appropriate here: this sandbox has
    nothing meaningful to flush on shutdown, and a grace period would
    only slow down the exact guarantee this phase exists to provide.
    Idempotent against a container that already exited on its own in
    the race window between the last poll and this call (OBSERVED:
    `docker.errors.APIError` status_code=409 "container is not
    running" -- treated as already-terminated, not an error); any
    other `APIError` (e.g. 404 if the container was removed by
    something else entirely) propagates as a genuine failure."""
    try:
        container.kill()
    except docker.errors.APIError as exc:
        if exc.status_code != 409:
            raise


async def _wait_bounded(
    container, *, timeout_s: float, job, session: AsyncSession
) -> tuple[dict, TerminationReason]:
    """Polls `container.wait(timeout=_POLL_INTERVAL_S)` in a loop --
    NEVER a single unbounded `container.wait()` and NEVER `asyncio.
    wait_for(asyncio.to_thread(container.wait), timeout=...)` either:
    the latter only cancels the AWAITING COROUTINE while the underlying
    OS thread pool worker stays blocked inside the synchronous SDK call
    indefinitely (this phase's own explicit warning) -- docker-py's
    OWN `timeout=` kwarg, by contrast, is a real client-side HTTP
    read-timeout that aborts the blocking call and returns control to
    Python within `_POLL_INTERVAL_S`, every iteration (OBSERVED, real
    probe: `container.wait(timeout=3)` against a `sleep 30` container
    raised after exactly 3.0s, and the container was confirmed still
    RUNNING server-side afterward -- a client-side timeout has no
    effect on the daemon or the container process).

    Each timed-out iteration checks, in this fixed order: (1) has
    `timeout_s` elapsed since this call started -- terminate, return
    "timeout"; (2) else, does a FRESH read of `job`'s own row (`await
    session.refresh(job)` -- deliberately NOT `ExecutionJobRepository.
    get_by_id()`, which is `AsyncSession.get()` under the hood and
    silently returns the SAME cached identity-map object once `job` has
    already been loaded on this session, never re-querying the database
    at all; `refresh()` is what actually forces a new SELECT and picks
    up a concurrent cancellation request -- ROOT-CAUSED via a real,
    failing test in this phase, not assumed) show `CANCEL_REQUESTED` --
    terminate, return "cancelled"; (3)
    else, loop again. This fixed check order is what makes a
    simultaneous timeout-deadline-elapsed + cancellation-requested
    resolve deterministically to "timeout" -- there is no actual race
    to arbitrate, since this is sequential code in one loop, not two
    concurrent writers (see the phase report's Terminal Race Semantics
    section).

    If `container.wait()` ever returns a real `{"StatusCode": N}` (the
    container genuinely exited -- Docker's own authoritative truth),
    returns immediately with "natural", deliberately WITHOUT re-
    checking for a pending cancellation request first: once a
    container has already finished on its own, there is nothing left
    to terminate, and the real scientific result it produced is still
    the most useful, correct thing to report.

    Raises `docker.errors.DockerException` for anything else -- a
    genuinely unexpected connection error, or any Docker-level failure
    from `_terminate_container`/the post-terminate `wait()` -- for the
    caller's existing DockerException handling to catch uniformly
    (Sprint 16 Phase 7B.28's T12: "bounded diagnostic, no false
    success, no exception hidden").
    """
    deadline = time.monotonic() + timeout_s

    while True:
        try:
            wait_result = await asyncio.to_thread(container.wait, timeout=_POLL_INTERVAL_S)
            return wait_result, "natural"
        except requests.exceptions.ConnectionError as exc:
            if not _is_client_poll_timeout(exc):
                raise docker.errors.DockerException(
                    f"unexpected connection error while waiting: {exc}"
                ) from exc

            reason: TerminationReason | None = None
            if time.monotonic() >= deadline:
                reason = "timeout"
            else:
                await session.refresh(job)
                if job.status == ExecutionJobStatus.CANCEL_REQUESTED:
                    reason = "cancelled"
            if reason is None:
                continue

            await asyncio.to_thread(_terminate_container, container)
            # Unbounded is safe here: the container is already killed
            # or already exited on its own (OBSERVED: `wait()` after
            # `kill()` returns in ~5ms) -- this is not the unbounded
            # wait this phase exists to eliminate.
            wait_result = await asyncio.to_thread(container.wait)
            return wait_result, reason


async def _finalize_exited_attempt(
    client,
    container,
    *,
    attempt,
    job,
    session: AsyncSession,
    termination_reason: TerminationReason,
    timeout_s: float,
    wait_result: dict | None = None,
) -> ExecutionOutcome:
    """The shared terminal-write tail for a container Docker itself
    reports as exited (Sprint 16 Phase 7B.29) -- used by BOTH
    `execute_approved_launch` (the live path, which already knows
    `termination_reason` from its own `_wait_bounded` call) and
    `reconcile_attempt` (which either re-attached `_wait_bounded` itself,
    or classified `termination_reason` from real Docker timestamps via
    `_classify_termination_from_evidence` -- see that function's own
    docstring for why that reconstruction is honest, not a guess).

    `inspect()` is always re-read here and is authoritative for
    ExitCode/FinishedAt/StartedAt (OBSERVED, Phase 7B.25 spike) -- never
    trusted from an earlier in-memory `wait_result` alone. `wait_result`
    is optional and used only for the diagnostic exit-code-mismatch log
    Phase 7B.25 already had; `reconcile_attempt`'s "container already
    exited, DB hadn't caught up" path has no `wait_result` of its own
    (no wait was ever performed by this process) and passes `None`.

    `ExecutionJobRepository.finalize_running_job`'s atomic conditional
    `UPDATE` (`WHERE status IN ('RUNNING', 'CANCEL_REQUESTED', 'VALIDATING')`)
    is the ONE serialization point in this function: only the caller whose
    call to it actually claims the row (a) treats `succeeded`/`diagnostic`
    as authoritative, (b) is allowed to create a NEW result `Asset` (via
    `_find_or_create_result_asset`) if none exists yet, and (c) removes
    the container. A caller that loses this race -- the normal launcher
    vs. a reconciler, or two concurrent reconcilers, Sprint 16 Phase
    7B.29 races A/B/D -- does none of those three things and returns.

    Cleanup is deliberately gated behind the SAME win/lose branch as log
    collection and asset creation (T9, discovered via a REAL race: two
    concurrent reconcilers both calling `_best_effort_remove` -- even
    though `remove()` itself is independently idempotent -- raced a
    still-in-progress `container.logs()` read on the winner's side into a
    real `docker.errors.APIError(409)` ("removal of container ... is
    already in progress"), observed directly against the daemon before
    this gating was added). Only the winner ever reads logs from or
    removes a given container; the loser touches Docker exactly zero
    times once it has lost, eliminating that race by construction rather
    than by tolerating the 409.
    """
    inspected = await asyncio.to_thread(_reload_and_get_state, container)
    exit_code = inspected["ExitCode"]
    if wait_result is not None and exit_code != wait_result["StatusCode"]:
        # Never observed in the spike (they matched every time) -- if it
        # ever happens, inspect() is authoritative (spike notes §7).
        logger.warning(
            "execution_docker_exit_code_mismatch",
            attempt_id=str(attempt.id),
            wait_status_code=wait_result["StatusCode"],
            inspect_exit_code=exit_code,
        )

    attempt.status = ExecutionAttemptStatus.EXITED
    attempt.exit_code = exit_code
    attempt.completed_at = _parse_docker_timestamp(inspected["FinishedAt"])
    if attempt.started_at is None:
        attempt.started_at = _parse_docker_timestamp(inspected["StartedAt"])
    await session.commit()
    logger.info(
        "execution_docker_exit_code",
        attempt_id=str(attempt.id),
        exit_code=exit_code,
        termination_reason=termination_reason,
    )

    # Sprint 16 Phase 7B.28/7B.29: exactly one of SUCCEEDED/FAILED/
    # TIMED_OUT/CANCELLED, decided by `termination_reason`. A timeout or
    # cancellation is NEVER "succeeded", regardless of exit code.
    if termination_reason == "natural":
        target_status = ExecutionJobStatus.SUCCEEDED if exit_code == 0 else ExecutionJobStatus.FAILED
        diagnostic = (
            None
            if exit_code == 0
            else _bounded_text(f"execution exited with code {exit_code}; result carries stdout/stderr")
        )
    elif termination_reason == "timeout":
        target_status = ExecutionJobStatus.TIMED_OUT
        diagnostic = _bounded_text(
            f"execution exceeded its {timeout_s}s timeout and was terminated "
            f"(exit_code={exit_code}); result carries stdout/stderr"
        )
    else:  # "cancelled"
        target_status = ExecutionJobStatus.CANCELLED
        diagnostic = _bounded_text(
            f"execution was cancelled by request and terminated (exit_code={exit_code}); "
            f"result carries stdout/stderr"
        )
    succeeded = termination_reason == "natural" and exit_code == 0

    claimed_job = await ExecutionJobRepository(session).finalize_running_job(
        job.id, status=target_status, reason=diagnostic, completed_at=attempt.completed_at
    )

    if claimed_job is not None:
        stdout, stdout_truncated = await _read_bounded_logs(container, stdout=True, stderr=False)
        stderr, stderr_truncated = await _read_bounded_logs(container, stdout=False, stderr=True)
        result_asset_id = await _find_or_create_result_asset(
            session,
            project_id=job.project_id,
            owner_id=job.owner_id,
            attempt_id=attempt.id,
            job_id=job.id,
            exit_code=exit_code,
            stdout=stdout,
            stdout_truncated=stdout_truncated,
            stderr=stderr,
            stderr_truncated=stderr_truncated,
        )
        if diagnostic is not None:
            attempt.error_message = diagnostic
            await session.commit()
        await _best_effort_remove(client, container, attempt_id=attempt.id)
    else:
        logger.info(
            "execution_job_finalize_lost_race", attempt_id=str(attempt.id), job_id=str(job.id)
        )
        existing = await AssetRepository(session).find_by_execution_attempt(attempt.id)
        result_asset_id = existing.id if existing is not None else None

    return ExecutionOutcome(
        succeeded=succeeded, exit_code=exit_code, result_asset_id=result_asset_id, diagnostic=diagnostic
    )


async def execute_approved_launch(
    approved: ApprovedLaunchSpec,
    *,
    attempt_id: uuid.UUID,
    container_name: str,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    session: AsyncSession,
) -> ExecutionOutcome:
    """Drives ONE `ExecutionAttempt(PENDING_CREATE)` through the real
    Docker lifecycle to a durable terminal state. See the module
    docstring for the exact persist ordering.

    BOUNDED WAIT (Sprint 16 Phase 7B.28): `_wait_bounded()` replaces the
    single unbounded `container.wait()` Phase 7B.25/26/27 used -- a
    container that never exits is now terminated (`kill()`) once
    `approved.timeout_s` elapses, or as soon as a durable cancellation
    request (`ExecutionJob.status == CANCEL_REQUESTED`) is observed,
    whichever this function's own polling loop notices first. See
    `_wait_bounded`'s own docstring for why this is safe against the
    "thread stays blocked forever" trap an `asyncio.wait_for()` wrapper
    around an unbounded call would not have avoided.

    `ExecutionJob.status` is written here for the first time past
    `VALIDATING` (Sprint 16 Phase 7B.28): `RUNNING` once the container
    starts (so a cancellation request has a real row to atomically
    claim against, mirroring `ExecutionJobRepository.claim_pending_job`'s
    existing conditional-`UPDATE` shape), then exactly one of
    `SUCCEEDED`/`FAILED`/`TIMED_OUT`/`CANCELLED` at the terminal point.

    Every Docker-level failure (ImageNotFound, name collision/409,
    start failure, daemon transport failure, or a genuine failure
    during the bounded wait/terminate sequence) is caught, diagnosed
    onto `attempt.error_message` (bounded), and returns a non-raising
    `ExecutionOutcome(succeeded=False, ...)` -- matching
    `recover_execution_job_retry`/`launch_execution_job`'s established
    "no exception escapes the task" contract from Phase 7B.21/7B.22.
    Only a genuinely unexpected exception (not one of the anticipated
    Docker error types) propagates, after a best-effort cleanup
    attempt.
    """
    attempts = ExecutionAttemptRepository(session)
    attempt = await attempts.get_by_id(attempt_id)
    if attempt is None:
        raise ValueError(f"execution attempt {attempt_id} does not exist")

    jobs = ExecutionJobRepository(session)
    job = await jobs.get_by_id(job_id)
    if job is None:
        raise ValueError(f"execution job {job_id} does not exist")

    try:
        client = await asyncio.to_thread(docker.from_env)
    except docker.errors.DockerException as exc:
        diagnostic = _bounded_text(f"Docker daemon unreachable: {exc}")
        attempt.error_message = diagnostic
        await session.commit()
        logger.error("execution_docker_daemon_unreachable", attempt_id=str(attempt_id))
        return ExecutionOutcome(succeeded=False, exit_code=None, result_asset_id=None, diagnostic=diagnostic)

    create_kwargs = _docker_create_kwargs(approved, container_name=container_name)

    logger.info("execution_docker_create_started", attempt_id=str(attempt_id), container_name=container_name)
    try:
        container = await asyncio.to_thread(client.containers.create, **create_kwargs)
    except docker.errors.ImageNotFound as exc:
        diagnostic = _bounded_text(f"image not found: {exc}")
        attempt.error_message = diagnostic  # stays PENDING_CREATE -- no container ever existed
        await session.commit()
        logger.error("execution_docker_create_image_not_found", attempt_id=str(attempt_id))
        return ExecutionOutcome(succeeded=False, exit_code=None, result_asset_id=None, diagnostic=diagnostic)
    except docker.errors.APIError as exc:
        status_code = getattr(exc, "status_code", None)
        diagnostic = _bounded_text(f"docker create failed (status={status_code}): {exc}")
        attempt.error_message = diagnostic  # stays PENDING_CREATE -- no container ever existed;
        # a 409 name collision in particular means a DIFFERENT container already owns this name
        # (a prior attempt), left completely unaffected -- OBSERVED, spike Test 4.
        await session.commit()
        logger.error(
            "execution_docker_create_failed", attempt_id=str(attempt_id), status_code=status_code
        )
        return ExecutionOutcome(succeeded=False, exit_code=None, result_asset_id=None, diagnostic=diagnostic)
    except docker.errors.DockerException as exc:
        diagnostic = _bounded_text(f"docker daemon transport failure during create: {exc}")
        attempt.error_message = diagnostic
        await session.commit()
        logger.error("execution_docker_create_transport_failure", attempt_id=str(attempt_id))
        return ExecutionOutcome(succeeded=False, exit_code=None, result_asset_id=None, diagnostic=diagnostic)

    logger.info("execution_docker_create_succeeded", attempt_id=str(attempt_id), container_id=container.id)

    # Durable BEFORE start() -- container_id must survive a crash here
    # (spike Test 10 Boundary A: the container already exists,
    # daemon-side, the instant create() returns; a future reconciler
    # can only find it by container_name unless this id is persisted
    # now).
    attempt.container_id = container.id
    attempt.status = ExecutionAttemptStatus.CREATED
    await session.commit()

    try:
        await _run_started_container(client, container, attempt=attempt, session=session)
    except docker.errors.DockerException:
        # An anticipated Docker-level start failure -- already diagnosed
        # onto attempt.error_message/status by _run_started_container
        # itself. Cleaned up and returned gracefully, matching every
        # other anticipated failure mode this function handles (no
        # exception escapes for a KNOWN Docker failure taxonomy).
        await _best_effort_remove(client, container, attempt_id=attempt_id)
        return ExecutionOutcome(
            succeeded=False, exit_code=None, result_asset_id=None, diagnostic=attempt.error_message
        )
    except Exception:
        # Genuinely unexpected -- not one of the anticipated Docker
        # error types. Cleanup attempted, but this propagates: it is
        # not safe to claim any particular outcome for something this
        # code did not anticipate.
        await _best_effort_remove(client, container, attempt_id=attempt_id)
        raise

    # Mirror the attempt's RUNNING transition onto the job (Sprint 16
    # Phase 7B.28) -- the first write to ExecutionJob.status past
    # VALIDATING; needed so a cancellation request has a real RUNNING
    # row to atomically claim against.
    job.status = ExecutionJobStatus.RUNNING
    job.started_at = attempt.started_at
    await session.commit()

    try:
        wait_result, termination_reason = await _wait_bounded(
            container, timeout_s=approved.timeout_s, job=job, session=session
        )
    except docker.errors.DockerException as exc:
        # A genuine daemon/API failure during wait/terminate (T12) --
        # bounded diagnostic, no false success, no exception hidden.
        # attempt.status becomes UNKNOWN (the enum's own documented
        # "cannot determine the real state" fallback, not invented) --
        # we do not know whether the container is stopped, still
        # running, or gone, so EXITED would overclaim knowledge we do
        # not have.
        diagnostic = _bounded_text(f"docker failure during wait/terminate: {exc}")
        attempt.status = ExecutionAttemptStatus.UNKNOWN
        attempt.error_message = diagnostic
        job.status = ExecutionJobStatus.FAILED
        job.reason = diagnostic
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        logger.error(
            "execution_docker_wait_or_terminate_failed", attempt_id=str(attempt_id), job_id=str(job_id)
        )
        await _best_effort_remove(client, container, attempt_id=attempt_id)
        return ExecutionOutcome(succeeded=False, exit_code=None, result_asset_id=None, diagnostic=diagnostic)

    # Sprint 16 Phase 7B.29: the terminal write itself -- attempt fields,
    # exactly-one-of SUCCEEDED/FAILED/TIMED_OUT/CANCELLED, and result-asset
    # persistence -- now lives in `_finalize_exited_attempt`, SHARED with
    # `reconcile_attempt` (a crashed/killed launcher's Docker-aware
    # recovery path). This call site is unchanged in behavior from Phase
    # 7B.28: `termination_reason` is deterministic because `_wait_bounded`
    # itself only ever returns ONE reason per call (see its own docstring
    # for why "natural" is never re-litigated against a pending
    # cancellation request).
    outcome = await _finalize_exited_attempt(
        client,
        container,
        attempt=attempt,
        job=job,
        session=session,
        termination_reason=termination_reason,
        timeout_s=approved.timeout_s,
        wait_result=wait_result,
    )

    return outcome


def _reload_and_get_state(container) -> dict:
    container.reload()
    return container.attrs["State"]


async def _run_started_container(client, container, *, attempt, session: AsyncSession) -> None:
    """`docker start()` through persisting `RUNNING` -- isolated so a
    start failure's cleanup/inspection logic stays in one place.

    Per the phase's explicit "NOTE ON START FAILURE": container_id is
    already durable (committed before this is ever called); on failure
    this inspects the REAL Docker state rather than assuming the
    container never started, and persists the most accurate status the
    CURRENT enum can represent -- `CREATED` (unchanged) if Docker
    confirms it never left `created`, `UNKNOWN` (the enum's own
    documented "cannot determine the real state" fallback) if inspect
    itself is inconclusive. No new enum value is invented.
    """
    try:
        await asyncio.to_thread(container.start)
    except docker.errors.DockerException as exc:
        try:
            state = await asyncio.to_thread(_reload_and_get_state, container)
        except docker.errors.DockerException:
            attempt.status = ExecutionAttemptStatus.UNKNOWN
            attempt.error_message = _bounded_text(f"start failed and container state is unknown: {exc}")
            await session.commit()
            raise

        if state["Status"] == "created":
            attempt.status = ExecutionAttemptStatus.CREATED  # unchanged -- accurate as-is
        else:
            attempt.status = ExecutionAttemptStatus.UNKNOWN
        attempt.error_message = _bounded_text(f"docker start failed: {exc} (observed State={state!r})")
        await session.commit()
        logger.error("execution_docker_start_failed", attempt_id=str(attempt.id))
        raise

    state = await asyncio.to_thread(_reload_and_get_state, container)
    attempt.status = ExecutionAttemptStatus.RUNNING
    attempt.started_at = _parse_docker_timestamp(state.get("StartedAt"))
    await session.commit()
    logger.info("execution_docker_start_succeeded", attempt_id=str(attempt.id))


async def _best_effort_remove(client, container, *, attempt_id: uuid.UUID) -> None:
    """Idempotent cleanup (OBSERVED: remove() on an already-gone
    container raises `docker.errors.NotFound` -- treated as already
    clean, never as a failure). A cleanup failure is logged, never
    raised -- it must not hide whatever the primary execution outcome
    already was, and durable state (container_id, status, exit_code)
    already persisted by this point is enough for a future reconciler
    to find and remove the orphan even if this process's own removal
    attempt fails."""
    try:
        await asyncio.to_thread(container.remove, force=True)
        logger.info("execution_docker_cleanup_succeeded", attempt_id=str(attempt_id))
    except docker.errors.NotFound:
        logger.info("execution_docker_cleanup_already_clean", attempt_id=str(attempt_id))
    except docker.errors.DockerException as exc:
        logger.warning(
            "execution_docker_cleanup_failed",
            attempt_id=str(attempt_id),
            error_type=type(exc).__name__,
        )


# =============================================================================
# Sprint 16 Phase 7B.29 -- Docker-aware crash recovery & reconciliation.
#
# `reconcile_attempt` is the single entry point: given one attempt_id, it
# asks the REAL Docker daemon what actually happened and reconciles durable
# AIKDAP state (`ExecutionAttempt`/`ExecutionJob`) to match -- covering a
# worker process that disappeared (crash, OOM, container restart) at ANY
# point in `execute_approved_launch`'s lifecycle, from just after
# `docker create()` through just before its own cleanup.
#
# What this does NOT do: create a second `ExecutionAttempt`, touch
# `attempt_number`, move a job back to `PENDING`, or invoke
# `prepare_retry_attempt`/`recover_interrupted_retry_attempt` -- none of
# those belong to recovering the lifecycle of an EXISTING attempt, which is
# this phase's entire scope.
# =============================================================================


def _classify_termination_from_evidence(attempt, job, state: dict) -> TerminationReason:
    """Reconstructs which of "natural"/"timeout"/"cancelled" produced an
    EXITED container the database never got to classify itself (Sprint 16
    Phase 7B.29) -- used only when `reconcile_attempt` finds a container
    Docker already reports as `exited` while the owning attempt's own
    terminal write never landed (T3/T4-shaped: the launcher died between
    the container finishing and its own persist).

    `_terminate_container` uses `kill()` (SIGKILL) identically for BOTH a
    timeout and a cancellation -- once exited, Docker's `ExitCode`/
    `OOMKilled`/`Dead` fields cannot, by themselves, distinguish "we
    killed it for timing out" from "we killed it because cancellation was
    requested" from "the script legitimately exited with code 137 on its
    own." This function resolves that ambiguity from the two REAL,
    durable facts that ARE still available, in this fixed priority order
    (mirroring `_wait_bounded`'s own fixed check order):

    1. `job.status == CANCEL_REQUESTED` -- a durable cancellation request
       is unambiguous: if one was ever made, the termination is
       attributed to it, matching `_wait_bounded`'s own precedent (a
       durable request is never silently dropped).
    2. Measured runtime (`FinishedAt - StartedAt`, both real Docker
       timestamps) >= the resource-class-derived `timeout_s` -- the SAME
       comparison `_wait_bounded`'s own deadline check performs, just
       computed after the fact from Docker's own timestamps instead of a
       live monotonic clock.
    3. Otherwise "natural" -- the container exited within its own time
       budget with no cancellation on record.

    Classified as INFERRED evidence (see the phase report), not OBSERVED:
    `_wait_bounded` never persists which branch it took internally, so
    this is the closest truthful reconstruction available from durable
    facts, not a replay of the original in-process decision.
    """
    if job.status == ExecutionJobStatus.CANCEL_REQUESTED:
        return "cancelled"

    timeout_s = _safe_resource_class_timeout(job)
    started = _parse_docker_timestamp(state.get("StartedAt"))
    finished = _parse_docker_timestamp(state.get("FinishedAt"))
    if started is not None and finished is not None and (finished - started).total_seconds() >= timeout_s:
        return "timeout"
    return "natural"


async def _finalize_job_status_only_from_attempt(attempt, job, session: AsyncSession) -> None:
    """Fixes a job stuck at `RUNNING`/`CANCEL_REQUESTED` whose OWN attempt
    is already `EXITED`/`UNKNOWN` but whose container is GONE (Sprint 16
    Phase 7B.29) -- the narrow crash window between `_finalize_exited_
    attempt`'s attempt-write and its own job-write, discovered only after
    the container was already removed (by a prior cleanup pass, or
    externally).

    No stdout/stderr can be recovered here: Docker's default log driver
    deletes a container's logs when the container is removed, and the
    container is already gone by the time this runs -- an accepted,
    honest limitation ("recover... where possible"), not an oversight.
    Classification uses the SAME evidence priority as
    `_classify_termination_from_evidence` (cancellation request, then
    measured-runtime-vs-timeout, then natural), just read from the
    attempt's own already-durable `started_at`/`completed_at`/`exit_code`
    instead of a fresh Docker inspect, since none is available.
    """
    if job.status == ExecutionJobStatus.CANCEL_REQUESTED:
        target_status = ExecutionJobStatus.CANCELLED
        diagnostic = _bounded_text(
            f"execution was cancelled by request and terminated (exit_code={attempt.exit_code}); "
            f"the container was already removed before reconciliation could recover its output"
        )
    else:
        timeout_s = _safe_resource_class_timeout(job)
        ran_long = (
            attempt.started_at is not None
            and attempt.completed_at is not None
            and (attempt.completed_at - attempt.started_at).total_seconds() >= timeout_s
        )
        if ran_long:
            target_status = ExecutionJobStatus.TIMED_OUT
            diagnostic = _bounded_text(
                f"execution exceeded its {timeout_s}s timeout and was terminated "
                f"(exit_code={attempt.exit_code}); the container was already removed before "
                f"reconciliation could recover its output"
            )
        elif attempt.exit_code == 0:
            target_status = ExecutionJobStatus.SUCCEEDED
            diagnostic = None
        else:
            target_status = ExecutionJobStatus.FAILED
            diagnostic = _bounded_text(
                f"execution exited with code {attempt.exit_code}; the container was already "
                f"removed before reconciliation could recover its output"
            )

    await ExecutionJobRepository(session).finalize_running_job(
        job.id,
        status=target_status,
        reason=diagnostic,
        completed_at=attempt.completed_at or datetime.now(timezone.utc),
    )


async def _reconcile_missing_container(attempt, job, session: AsyncSession) -> ReconciliationOutcome:
    """Handles every case where `client.containers.get(identity)` (or a
    subsequent `.reload()`) raised `docker.errors.NotFound` (Sprint 16
    Phase 7B.29) -- deliberately does NOT assume "missing = failure": what
    it means depends entirely on `attempt.status` at the moment of the
    lookup, exactly as the phase requires.
    """
    if attempt.status == ExecutionAttemptStatus.PENDING_CREATE:
        # Not one of the phase's 8 named cases (which all describe a
        # container that WAS created) -- reachable only if this function
        # is invoked directly against a pending attempt rather than via
        # `find_stale_active_attempts` (which never selects PENDING_CREATE
        # rows in the first place). A no-op: `reconcile_stale_pending_
        # execution_attempts` (Phase 7B.15) already owns diagnosing this
        # row; nothing Docker-specific to add.
        return ReconciliationOutcome("pending_create_no_container", attempt.status, job.status, None)

    if attempt.status in _TERMINAL_ATTEMPT_STATUSES:
        # Case 7 -- EXITED (or UNKNOWN) attempt, container already
        # removed. Legitimate: our own cleanup, or a prior reconciliation
        # pass, already ran. The only remaining gap this closes is a job
        # that never got its own terminal write (see
        # `_finalize_job_status_only_from_attempt`'s own docstring).
        if job.status in _IN_FLIGHT_JOB_STATUSES:
            await _finalize_job_status_only_from_attempt(attempt, job, session)
        return ReconciliationOutcome("already_terminal_no_container", attempt.status, job.status, None)

    # attempt.status is CREATED or RUNNING but Docker has no record of the
    # container at all -- Case 4 (RUNNING) and Case 5 (CREATED). Genuinely
    # lost evidence: no exit code, no logs, no way to know what actually
    # happened (a legitimate prior cleanup racing this same reconciliation
    # attempt is handled by `finalize_running_job`'s own atomic guard
    # below, not by guessing here). Never invented as success -- FAILED is
    # the only terminal ExecutionJobStatus that does not claim more than
    # "did not succeed," and ExecutionAttemptStatus.UNKNOWN is the enum's
    # own documented "cannot determine the real state" fallback.
    diagnostic = _bounded_text(
        "reconciliation found no matching Docker container for this attempt; "
        "the execution's outcome could not be determined"
    )
    attempt.status = ExecutionAttemptStatus.UNKNOWN
    attempt.error_message = diagnostic
    await session.commit()

    claimed = await ExecutionJobRepository(session).finalize_running_job(
        job.id, status=ExecutionJobStatus.FAILED, reason=diagnostic, completed_at=datetime.now(timezone.utc)
    )
    logger.warning(
        "execution_reconcile_container_lost", attempt_id=str(attempt.id), job_finalized=claimed is not None
    )
    final_job_status = claimed.status if claimed is not None else job.status
    return ReconciliationOutcome("lost_marked_unknown", attempt.status, final_job_status, None)


async def _reconcile_created_container(client, container, attempt, job, session: AsyncSession) -> ReconciliationOutcome:
    """Handles Docker `State.Status == "created"` (Sprint 16 Phase 7B.29,
    Case 1): the container exists but never started.
    """
    if attempt.status not in (ExecutionAttemptStatus.PENDING_CREATE, ExecutionAttemptStatus.CREATED):
        # DB claims MORE progress (RUNNING/EXITED/UNKNOWN) than Docker
        # shows (still "created", never started) -- not reachable under
        # the current single-writer lifecycle (a container never reverts
        # from running back to created). Preserved untouched: never
        # overwrite a more-advanced durable fact with a less-advanced
        # Docker observation.
        logger.warning(
            "execution_reconcile_docker_behind_db",
            attempt_id=str(attempt.id),
            attempt_status=attempt.status.value,
        )
        return ReconciliationOutcome("inconsistent_docker_status_preserved", attempt.status, job.status, None)

    if attempt.container_id is None:
        # create() succeeded daemon-side but the launcher crashed before
        # committing container_id -- recovered here via the deterministic
        # container_name lookup that got this function called at all.
        attempt.container_id = container.id
        attempt.status = ExecutionAttemptStatus.CREATED
        await session.commit()

    # The container exists but never started -- Docker's own timeout
    # clock (which only ever runs from `started_at`) never began, so
    # there is nothing to "wait out" and no safe way to resume execution
    # ourselves (that would be starting a NEW execution attempt in
    # substance, which this phase does not authorize). The honest
    # terminal state is UNKNOWN: this attempt never produced a real
    # result, but "EXITED" would overclaim a Docker fact that is not
    # true -- the container never actually ran.
    diagnostic = _bounded_text(
        "reconciliation found this attempt's container still at Docker state "
        "'created' -- it never started before the launching process was "
        "interrupted; no result was produced"
    )
    attempt.status = ExecutionAttemptStatus.UNKNOWN
    attempt.error_message = diagnostic
    await session.commit()

    claimed = await ExecutionJobRepository(session).finalize_running_job(
        job.id, status=ExecutionJobStatus.FAILED, reason=diagnostic, completed_at=datetime.now(timezone.utc)
    )
    await _best_effort_remove(client, container, attempt_id=attempt.id)
    final_job_status = claimed.status if claimed is not None else job.status
    return ReconciliationOutcome("recovered_created_abandoned", attempt.status, final_job_status, None)


async def _reconcile_running_container(
    client, container, attempt, job, session: AsyncSession, state: dict
) -> ReconciliationOutcome:
    """Handles Docker `State.Status == "running"` (Sprint 16 Phase 7B.29,
    Case 2): the container is genuinely still executing.
    """
    if attempt.status != ExecutionAttemptStatus.RUNNING:
        # start() succeeded daemon-side but the RUNNING write never
        # landed -- bring the attempt's own record up to date with
        # reality before doing anything else.
        attempt.status = ExecutionAttemptStatus.RUNNING
        if attempt.started_at is None:
            attempt.started_at = _parse_docker_timestamp(state.get("StartedAt"))
        attempt.container_id = attempt.container_id or container.id
        await session.commit()

    if job.status not in _IN_FLIGHT_JOB_STATUSES:
        if job.status in _TERMINAL_JOB_STATUSES:
            # The job was already finalized while a container is somehow
            # still alive -- never resurrect a finished job back to
            # RUNNING. The container is a leftover that must not keep
            # running; terminate and clean up, leave the job record
            # untouched (Sprint 16 Phase 7B.29: "do not reclassify a
            # completed result merely because cleanup was incomplete").
            await asyncio.to_thread(_terminate_container, container)
            await asyncio.to_thread(container.wait)
            await _best_effort_remove(client, container, attempt_id=attempt.id)
            return ReconciliationOutcome(
                "terminal_job_leftover_container_terminated", attempt.status, job.status, None
            )
        # job is PENDING/VALIDATING/LAUNCHING with a running container --
        # not reachable under the current single-writer launch protocol
        # (a container only ever exists once prepare_approved_launch has
        # already moved the job past those stages). Preserved untouched
        # rather than guessed at.
        logger.warning(
            "execution_reconcile_job_status_inconsistent",
            attempt_id=str(attempt.id),
            job_status=job.status.value,
        )
        return ReconciliationOutcome("inconsistent_job_status_preserved", attempt.status, job.status, None)

    # The normal orphan-crash case: job legitimately still RUNNING (or
    # CANCEL_REQUESTED), container legitimately still executing. Re-derive
    # the resource-class timeout (never persisted as `ApprovedLaunchSpec`,
    # only ever as `job.resource_class`) and re-attach `_wait_bounded`
    # with the REMAINING budget -- the same bounded-wait primitive the
    # live launcher itself uses, so a still-healthy execution is allowed
    # to finish naturally rather than being pre-emptively killed just
    # because the process managing it died. `_wait_bounded` itself
    # already handles an already-elapsed deadline (`remaining == 0`,
    # terminates within one poll interval) and an already-durable
    # CANCEL_REQUESTED (observed on its very first poll) -- no separate
    # branch is needed for either.
    timeout_s = _safe_resource_class_timeout(job)
    started_at = attempt.started_at or _parse_docker_timestamp(state.get("StartedAt"))
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds() if started_at else timeout_s
    remaining = max(timeout_s - elapsed, 0.0)

    wait_result, termination_reason = await _wait_bounded(
        container, timeout_s=remaining, job=job, session=session
    )
    outcome = await _finalize_exited_attempt(
        client,
        container,
        attempt=attempt,
        job=job,
        session=session,
        termination_reason=termination_reason,
        timeout_s=timeout_s,
        wait_result=wait_result,
    )
    return ReconciliationOutcome("recovered_running", attempt.status, job.status, outcome.result_asset_id)


async def reconcile_attempt(attempt_id: uuid.UUID, session: AsyncSession) -> ReconciliationOutcome:
    """Docker-aware crash recovery for ONE `ExecutionAttempt` (Sprint 16
    Phase 7B.29) -- reconciles durable AIKDAP state against the REAL
    Docker daemon after the process driving this attempt's lifecycle (a
    `launch_execution_job`/`recover_execution_job_retry` Celery task, or
    an earlier reconciliation attempt) has disappeared, or simply lost the
    race to another such process, without reaching a terminal write.

    Container identity (Sprint 16 Phase 7B.29 "CONTAINER IDENTITY"):
    `attempt.container_id` when present, else the deterministic
    `attempt.container_name` (persisted on every attempt row at creation,
    unlike `container_id`, which is NULL until `docker create()` is
    confirmed). Docker-py's `containers.get()` accepts either a name or an
    ID. Name and ID can never refer to two different attempts: `container_
    name` is a pure function of `attempt_id` alone
    (`app.modules.execution.service.container_name_for_attempt`) AND is
    DB-unique (`ExecutionAttempt.container_name`, `unique=True`) --
    together these mean at most one attempt row could ever have proposed
    a given name to Docker, and Docker itself separately enforces name
    uniqueness within the daemon; `container_id` is likewise DB-unique
    once set. There is structurally no way for this lookup to resolve to
    a container belonging to a different attempt.

    Idempotent by construction, safe under arbitrary concurrent callers
    (T9/T10/T15 -- two reconciliation workers on the same attempt, a live
    launcher racing a reconciler, a timeout/cancel race, repeated runs):
    every branch that WRITES a job's terminal status goes through
    `ExecutionJobRepository.finalize_running_job`'s atomic conditional
    `UPDATE ... WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')`, and
    every branch that creates a result `Asset` does so only after WINNING
    that same atomic claim (`_finalize_exited_attempt`) -- Postgres, not
    application timing, is the sole arbiter of which concurrent caller's
    write actually lands. Docker operations (`kill()`, `remove()`) are
    independently idempotent (OBSERVED, Phase 7B.25/7B.28 spikes:
    `kill()` on an already-dead container raises `APIError(409)`,
    `remove()` on an already-gone one raises `NotFound` -- both handled).

    Never creates a second `ExecutionAttempt`, never touches
    `attempt_number`, never moves a job back to `PENDING`/`VALIDATING`/
    `LAUNCHING`, and never calls `prepare_retry_attempt`/
    `recover_interrupted_retry_attempt` -- this function recovers the
    lifecycle of the EXISTING attempt only, exactly as scoped.

    Returns a `ReconciliationOutcome` whose `action` is one of:
    `attempt_not_found`, `job_not_found`, `daemon_unreachable_deferred`,
    `inspect_failed_deferred`, `pending_create_no_container`,
    `already_terminal_no_container`, `already_terminal_cleaned_up`,
    `lost_marked_unknown`, `recovered_created_abandoned`,
    `recovered_running`, `recovered_exited`,
    `terminal_job_leftover_container_terminated`,
    `inconsistent_job_status_preserved`,
    `inconsistent_docker_status_preserved`,
    `unexpected_docker_status_preserved`. Every `*_deferred`/
    `*_preserved` outcome deliberately makes NO durable write -- Docker
    daemon failures and unresolvable inconsistencies are left for a later
    reconciliation pass rather than resolved by a guess (Sprint 16 Phase
    7B.29's explicit "distinguish: retry later, mark UNKNOWN, preserve
    current state").
    """
    attempts = ExecutionAttemptRepository(session)
    attempt = await attempts.get_by_id(attempt_id)
    if attempt is None:
        return ReconciliationOutcome("attempt_not_found", None, None, None)

    jobs = ExecutionJobRepository(session)
    job = await jobs.get_by_id(attempt.execution_job_id)
    if job is None:
        return ReconciliationOutcome("job_not_found", attempt.status, None, None)

    try:
        client = await asyncio.to_thread(docker.from_env)
    except docker.errors.DockerException as exc:
        logger.warning(
            "execution_reconcile_daemon_unreachable", attempt_id=str(attempt_id), error_type=type(exc).__name__
        )
        return ReconciliationOutcome("daemon_unreachable_deferred", attempt.status, job.status, None)

    identity = attempt.container_id or attempt.container_name
    try:
        container = await asyncio.to_thread(client.containers.get, identity)
        state = await asyncio.to_thread(_reload_and_get_state, container)
    except docker.errors.NotFound:
        # Covers BOTH `containers.get()` itself 404ing and the container
        # being removed by something else in the gap before `.reload()`
        # (Sprint 16 Phase 7B.29 T11).
        return await _reconcile_missing_container(attempt, job, session)
    except docker.errors.APIError as exc:
        logger.warning(
            "execution_reconcile_inspect_failed",
            attempt_id=str(attempt_id),
            status_code=getattr(exc, "status_code", None),
        )
        return ReconciliationOutcome("inspect_failed_deferred", attempt.status, job.status, None)

    if attempt.status in _TERMINAL_ATTEMPT_STATUSES:
        # Case 6 -- EXITED (or UNKNOWN) attempt, container still present.
        # Cleanup-only by default; also closes the job-lagging-behind-
        # attempt gap (a crash between `_finalize_exited_attempt`'s two
        # commits) by reusing the container that is STILL available here
        # to recover output, unlike the container-already-gone variant in
        # `_reconcile_missing_container`.
        if job.status in _IN_FLIGHT_JOB_STATUSES:
            # `_finalize_exited_attempt` owns cleanup itself here (gated
            # behind the SAME win/lose branch as its log read -- see its
            # own docstring's T9 note), so no separate `_best_effort_
            # remove` call follows it in this branch.
            termination_reason = _classify_termination_from_evidence(attempt, job, state)
            await _finalize_exited_attempt(
                client,
                container,
                attempt=attempt,
                job=job,
                session=session,
                termination_reason=termination_reason,
                timeout_s=_safe_resource_class_timeout(job),
            )
        else:
            # The job was already fully terminal -- no finalize call was
            # made, so this IS the sole cleanup attempt for this pass.
            await _best_effort_remove(client, container, attempt_id=attempt_id)
        return ReconciliationOutcome("already_terminal_cleaned_up", attempt.status, job.status, None)

    docker_status = state["Status"]

    if docker_status == "exited":
        # Case 3/4 -- the container finished, but the attempt's own
        # terminal write never landed. Cleanup is handled INSIDE
        # `_finalize_exited_attempt`, gated to the race winner only.
        termination_reason = _classify_termination_from_evidence(attempt, job, state)
        outcome = await _finalize_exited_attempt(
            client,
            container,
            attempt=attempt,
            job=job,
            session=session,
            termination_reason=termination_reason,
            timeout_s=_safe_resource_class_timeout(job),
        )
        return ReconciliationOutcome("recovered_exited", attempt.status, job.status, outcome.result_asset_id)

    if docker_status == "running":
        return await _reconcile_running_container(client, container, attempt, job, session, state)

    if docker_status == "created":
        return await _reconcile_created_container(client, container, attempt, job, session)

    # "paused"/"restarting"/"removing"/"dead" -- never induced by this
    # application's own launch flow (no pause/restart/remove-while-running
    # call exists anywhere in this codebase). Not one of the phase's 8
    # named cases and not exercised by the test matrix; preserved
    # untouched rather than guessed at, matching every other
    # not-reachable-under-current-design branch above.
    logger.warning(
        "execution_reconcile_unexpected_docker_status", attempt_id=str(attempt_id), docker_status=docker_status
    )
    return ReconciliationOutcome("unexpected_docker_status_preserved", attempt.status, job.status, None)
