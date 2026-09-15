"""Sprint 9J: recovers `ResearchRun` rows orphaned by a worker
interruption.

`ResearchExecutionService.execute()` sets `status=RUNNING` before doing
any real work and only reaches `_complete()`/`_fail()` if its own
Python process survives long enough to run them (see
`app.modules.research.service`). A worker crash, an OOM kill, or a
container restart mid-run skips both of those, and nothing in the
architecture ever revisits the row afterward -- confirmed live: a run
from an earlier interrupted manual test was still sitting at `RUNNING`
days later, with no active task claiming it.

This module does not run automatically as a cron: like
`app.workers.scheduler`'s existing `retry_failed_tasks`, this project
has no active Celery Beat schedule (`celery_app.py` defines no
`beat_schedule`, and no `beat` service exists in `docker-compose.yml`),
so adding one just for this single lightweight check would be
infrastructure the measurements do not justify. Instead,
`reconcile_stale_research_runs_on_startup()` runs once per worker
process, via Celery's own `worker_ready` signal (wired up in
`app.workers.worker`) -- the same "startup reconciliation" pattern
Sprint 9H already established for the API process
(`app.core.llm.startup_validation.run_startup_validation`, run from
FastAPI's lifespan). A worker that crashed and restarted reconciles its
own orphans the moment it comes back up; a long-lived worker that never
crashes never needs to.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging.logger import get_logger
from app.database.session import async_session_factory
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetType
from app.modules.assets.models import Asset
from app.modules.execution.repository import ExecutionAttemptRepository, ExecutionJobRepository
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun

logger = get_logger(__name__)

#: Persisted on every run this reconciles. Deliberately free of stack
#: traces, task ids, or infrastructure detail -- this string reaches
#: `GET /research/runs/{id}` unauthenticated-adjacent to nothing (the
#: endpoint requires auth, but `error_message` is still user-facing),
#: so it says what happened without saying anything about *why* the
#: worker died.
STALE_RUN_FAILURE_REASON = (
    "Research run marked failed during stale-run reconciliation after "
    "worker interruption."
)


async def reconcile_stale_research_runs() -> int:
    """Fail every `ResearchRun` stuck at `RUNNING` past the stale
    threshold. Returns the number of runs reconciled.

    Scoped, not blind: only rows that are *both* `status=RUNNING` *and*
    `started_at` older than `settings.research_run_stale_after_seconds`
    are touched. `execute()` sets `started_at` fresh at the top of
    every attempt -- including every Celery-level retry -- so a run
    that is genuinely still in progress always has a recent
    `started_at` and can never match this query, no matter how long
    its *first* attempt was queued for.

    Idempotent by construction rather than by a lock: the WHERE clause
    only ever matches rows still at `RUNNING`, and this function is the
    only thing that ever moves a row *out* of `RUNNING` without also
    completing it -- so a second call in the same process, or a second
    worker process reconciling at the same moment, finds nothing left
    to touch (or, in the concurrent case, a normal per-row database
    update race that Postgres itself resolves safely; no additional
    locking is added for a single-worker-instance deployment where that
    race does not occur in practice).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.research_run_stale_after_seconds
    )

    async with async_session_factory() as session:
        result = await session.execute(
            select(ResearchRun).where(
                ResearchRun.status == ResearchRunStatus.RUNNING,
                ResearchRun.started_at < cutoff,
            )
        )
        stale_runs = list(result.scalars().all())

        now = datetime.now(timezone.utc)
        for run in stale_runs:
            # A real, non-fabricated number: how long this row actually
            # existed as "running" before reconciliation closed it out.
            # Not a claim about when the underlying work actually
            # stopped -- the worker may have died earlier -- only about
            # when this row was last known to be in progress.
            duration_ms = (
                int((now - run.started_at).total_seconds() * 1000)
                if run.started_at
                else None
            )
            run.status = ResearchRunStatus.FAILED
            run.error_message = STALE_RUN_FAILURE_REASON
            run.completed_at = now
            run.duration_ms = duration_ms
            logger.warning(
                "research_run_reconciled_stale",
                run_id=str(run.id),
                started_at=run.started_at.isoformat() if run.started_at else None,
                stale_after_seconds=settings.research_run_stale_after_seconds,
            )

        if stale_runs:
            await session.commit()

    return len(stale_runs)


#: Persisted on every stale `PENDING_CREATE` attempt this reconciles.
#: Fixed and complete -- no interpolation of any kind, so nothing from
#: the database (container name, job id, project name) can ever reach
#: this string, and no log-injection or user-content-echo path exists
#: through it. Deliberately worded as an observation about what AIKDAP
#: has NOT seen, never as a claim about Docker: this code has not
#: contacted a daemon and does not know whether a container exists.
STALE_ATTEMPT_DIAGNOSTIC = (
    "Execution attempt remained pending-create beyond reconciliation threshold."
)


async def reconcile_stale_pending_execution_attempts() -> int:
    """Record a bounded diagnostic on every `ExecutionAttempt` still at
    `PENDING_CREATE` past the stale threshold. Returns the number of
    attempts NEWLY marked by this call.

    The gap this closes (Sprint 16 Phase 7B.15): `prepare_approved_launch()`
    commits the attempt row before any Docker call, on purpose, so a
    launcher that dies before or during that call leaves durable evidence.
    Nothing revisited those rows -- structurally the same orphan Sprint 9J
    found for `ResearchRun`, which is why this mirrors
    `reconcile_stale_research_runs` rather than inventing its own shape.

    What it deliberately does NOT do, and why. It does not change
    `status`: an attempt stuck at `PENDING_CREATE` means only that AIKDAP
    never observed a successful container creation within the expected
    window -- NOT that the container is absent, NOT that Docker failed,
    NOT that the launcher crashed. Moving the row to `CREATED`/`EXITED`/
    `UNKNOWN` would be asserting one of those, and only a Docker-aware
    reconciler that has actually queried the daemon can. It does not
    create an attempt, touch `attempt_number`, or return the job to
    `PENDING`: retry allocation still needs its own claim protocol (Phase
    7B.12 Part 9) and does not exist yet. This slice records what is
    known and stops.

    Idempotent by value, not by a lock or a state change. Because the row
    keeps its `PENDING_CREATE` status, the selection query keeps matching
    it on every subsequent run -- so idempotency cannot come from the
    WHERE clause the way it does for research runs, and is enforced
    explicitly instead: an attempt already carrying this exact diagnostic
    is skipped, so no UPDATE is emitted, `updated_at` does not move, and
    the message can never accumulate. Run twice, run a hundred times: the
    row after run 1 is byte-identical to the row after run N, and the
    return value drops to 0.

    Concurrency is left as a future concern, matching the precedent: two
    reconcilers racing on the same row would both write the same constant
    to the same column, which Postgres serialises harmlessly. No
    ownership or claim protocol is introduced -- there is nothing here
    worth claiming, because the operation neither consumes the row nor
    triggers anything.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.execution_attempt_stale_after_seconds
    )

    async with async_session_factory() as session:
        stale_attempts = await ExecutionAttemptRepository(session).find_stale_pending_attempts(cutoff)

        marked = 0
        for attempt in stale_attempts:
            if attempt.error_message == STALE_ATTEMPT_DIAGNOSTIC:
                continue
            attempt.error_message = STALE_ATTEMPT_DIAGNOSTIC
            marked += 1
            logger.warning(
                "execution_attempt_reconciled_stale",
                attempt_id=str(attempt.id),
                execution_job_id=str(attempt.execution_job_id),
                created_at=attempt.created_at.isoformat(),
                stale_after_seconds=settings.execution_attempt_stale_after_seconds,
            )

        if marked:
            await session.commit()

    return marked


#: Persisted on every stale `VALIDATING` job this reconciles. Fixed and
#: complete -- no interpolation, so no job id, project id, owner id, or
#: `LaunchRequest` parameter can ever reach this string. States only
#: what AIKDAP knows: the job stayed in `VALIDATING` longer than
#: expected. Never claims validation failed, Docker failed, no attempt
#: exists, or the launcher crashed -- none of those are knowable from
#: age alone.
STALE_VALIDATING_JOB_DIAGNOSTIC = (
    "Execution job remained in validating state beyond reconciliation threshold."
)


async def reconcile_stale_validating_execution_jobs() -> int:
    """Record a bounded diagnostic on every `ExecutionJob` still at
    `VALIDATING` past the stale threshold. Returns the number of jobs
    NEWLY marked by this call.

    The gap this closes (Sprint 16 Phase 7B.16): Phase 7B.10's
    documented failure policy leaves a claimed job at `VALIDATING`
    forever on a guard/resolver rejection, an attempt-creation failure,
    or a crash anywhere in `prepare_approved_launch()` -- and nothing
    ever revisited those rows. Mirrors
    `reconcile_stale_pending_execution_attempts` in shape, one level up
    the same lifecycle Phase 7B.12 identified as having two independent
    clocks: a job's status and one attempt's relationship to a
    container.

    What it deliberately does NOT do, and why. It does not change
    `status`: a job stuck at `VALIDATING` proves only that AIKDAP has
    not observed it leave that state within the expected window -- NOT
    that validation failed, NOT that Docker failed, NOT that no attempt
    was ever created, NOT that the launcher crashed. Moving the row to
    `FAILED` would assert a specific one of those causes without
    evidence for it. It does not create an `ExecutionAttempt`, does not
    increment `attempt_number`, and does not return the job to
    `PENDING` -- retry allocation still needs its own claim protocol
    (Phase 7B.12 Part 9) and does not exist yet. It also never queries
    `ExecutionAttempt` at all: whether a `PENDING_CREATE` attempt
    already exists for this job is irrelevant to this function and
    reveals nothing about Docker state either way, so inspecting it
    would only invite exactly the inference this design forbids.

    Idempotent by explicit value check, not by relying on the query's
    own exclusion. `find_stale_validating_jobs` filters on
    `updated_at`, and writing `reason` here necessarily bumps
    `updated_at` too (same `onupdate=func.now()` mechanism that marks
    the original claim) -- so, incidentally, a freshly-marked row also
    stops matching the query until the threshold elapses again. That
    incidental exclusion is NOT what this function relies on for
    correctness: the explicit `reason == STALE_VALIDATING_JOB_DIAGNOSTIC`
    skip below is what actually prevents a re-write (and guards the
    same-moment concurrent-reconciler race the query's own timing
    cannot). Net effect either way: run twice within the threshold
    window, second run marks 0; a job that stays stuck long enough to
    cross the threshold again is legitimately re-selected but still
    marks 0 once re-checked, because nothing new is known.

    One documented consequence of reusing `updated_at` for both the
    detection timestamp and the diagnostic write: after this function
    marks a row, `updated_at` no longer means "time of entering
    VALIDATING" -- it means "time last reconciled". Nothing in the
    current architecture reads `updated_at` for any purpose other than
    this query, so this is an accepted, harmless consequence of not
    introducing a dedicated `validating_at` column, not an oversight.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.execution_job_validating_stale_after_seconds
    )

    async with async_session_factory() as session:
        stale_jobs = await ExecutionJobRepository(session).find_stale_validating_jobs(cutoff)

        marked = 0
        for job in stale_jobs:
            if job.reason == STALE_VALIDATING_JOB_DIAGNOSTIC:
                continue
            job.reason = STALE_VALIDATING_JOB_DIAGNOSTIC
            marked += 1
            logger.warning(
                "execution_job_reconciled_stale_validating",
                job_id=str(job.id),
                updated_at=job.updated_at.isoformat(),
                stale_after_seconds=settings.execution_job_validating_stale_after_seconds,
            )

        if marked:
            await session.commit()

    return marked


async def reconcile_stale_launching_execution_jobs() -> int:
    """Detects stale `LAUNCHING` jobs and enqueues one recovery task per
    job (Sprint 16 Phase 7B.19/7B.20 design, implemented 7B.21,
    wired 7B.22). Stays detection-only, as designed: this function runs
    exactly one query and one `.delay()` per matched row. The guard
    pipeline and the claim-and-materialize transaction never run on this
    path -- they live entirely inside `workers.tasks.
    recover_execution_job_retry` -> `service.
    recover_interrupted_retry_attempt`, off this event loop, so this
    reconciler can be safely called from the worker's `worker_ready`
    startup path (Phase 7B.17's "fast, never-raises" contract) even
    though recovery itself does real guard-pipeline I/O.

    Staleness here (`updated_at < cutoff`, reusing
    `execution_job_validating_stale_after_seconds` -- the same
    structural-span reasoning as the `VALIDATING` case applies, since
    materializing a retry involves no Docker wait either) is a
    pre-filter only, not the safety mechanism: `_claim_launching_for_
    recovery`'s lock-and-recheck, inside the task, is what actually
    prevents duplicate allocation, independent of how precisely this
    cutoff was chosen.

    Never raises: the query and the `.delay()` calls (broker publish
    only -- the same fire-and-forget contract every other `.delay()`
    call site in this codebase relies on) are both inside one `try`, so
    a DB or broker failure is caught and logged rather than propagated.
    Returns the number of tasks enqueued.
    """
    from app.workers.tasks import recover_execution_job_retry

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.execution_job_validating_stale_after_seconds
    )

    try:
        async with async_session_factory() as session:
            stale_jobs = await ExecutionJobRepository(session).find_stale_launching_jobs(cutoff)

        for job in stale_jobs:
            recover_execution_job_retry.delay(str(job.id))
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "execution_job_launching_reconciliation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return 0

    if stale_jobs:
        logger.warning("execution_job_launching_recovery_enqueued", count=len(stale_jobs))

    return len(stale_jobs)


async def reconcile_stale_docker_managed_attempts() -> int:
    """Detects stale `CREATED`/`RUNNING` attempts and enqueues one
    Docker-aware reconciliation task per attempt (Sprint 16 Phase 7B.29).
    Stays detection-only, mirroring `reconcile_stale_launching_execution_
    jobs`'s exact shape: this function runs exactly one query and one
    `.delay()` per matched row, never touches Docker itself, and never
    imports the `docker` SDK -- `execution_launcher.launcher` remains the
    only module in this application permitted to (see that module's own
    docstring and `test_no_router_service_reconciliation_or_task_module_
    imports_docker`, which enumerates this file by name).

    The real Docker inspection, kill/wait, cleanup, and result-recovery
    logic lives entirely inside `execution_launcher.launcher.
    reconcile_attempt`, run off this event loop inside `workers.tasks.
    reconcile_execution_attempt` -- the SAME "detect cheaply here, do the
    real I/O in a dedicated task" split Phase 7B.19/7B.21/7B.22 already
    established for retry recovery, reused rather than reinvented because
    it already solves this phase's own stated concerns: startup latency
    (this query is cheap and Docker-free), daemon availability (a down
    daemon only fails the per-attempt task, never startup), and duplicate
    execution across multiple workers (`execution_launcher.launcher.
    reconcile_attempt`'s own atomic `finalize_running_job` claim makes two
    workers processing the same attempt safe by construction, independent
    of how many times either enqueues it).

    Never raises: the query and the `.delay()` calls (broker publish
    only) are both inside one `try`, matching every other startup-path
    reconciler in this module. Returns the number of tasks enqueued.
    """
    from app.workers.tasks import reconcile_execution_attempt

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.execution_attempt_stale_after_seconds)

    try:
        async with async_session_factory() as session:
            stale_attempts = await ExecutionAttemptRepository(session).find_stale_active_attempts(cutoff)

        for attempt in stale_attempts:
            reconcile_execution_attempt.delay(str(attempt.id))
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "execution_attempt_docker_reconciliation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return 0

    if stale_attempts:
        logger.warning("execution_attempt_docker_reconciliation_enqueued", count=len(stale_attempts))

    return len(stale_attempts)


async def reconcile_execution_jobs_on_startup() -> None:
    """Entry point for the worker's `worker_ready` signal handler (Sprint
    16 Phase 7B.17, extended 7B.29) -- runs the three independently-proven
    execution reconcilers once per worker startup. Only wiring: none of
    `reconcile_stale_validating_execution_jobs`,
    `reconcile_stale_pending_execution_attempts`, or
    `reconcile_stale_docker_managed_attempts` is modified by this
    function.

    Order: stale `VALIDATING` jobs, then stale `PENDING_CREATE` attempts,
    then stale `CREATED`/`RUNNING` (Docker-managed) attempts -- the
    natural read order for anyone scanning startup logs, following the
    lifecycle stage each covers earlier-to-later. The three queries are
    otherwise fully independent (no `WHERE` clause depends on another's
    result), so no dependency ordering is actually required for
    correctness, only for readability.

    Each reconciler has its OWN `try`/`except` -- deliberately not one
    `try` wrapping all three, so a failure in any one can never prevent
    the others from running, matching `reconcile_stale_research_runs_on_
    startup`'s "never raises" contract but applied to three independent
    operations instead of one. A reconciler's own summary count stays
    `None` if it raised, so the final summary distinguishes "ran, found
    nothing" (`0`) from "did not complete" (`None`) without inventing a
    third return value on the reconcilers themselves.

    Never raises, by construction: every branch that could propagate is
    caught individually. A worker that fails all three reconciliations
    still finishes starting and accepts tasks.
    """
    jobs_marked: int | None = None
    try:
        jobs_marked = await reconcile_stale_validating_execution_jobs()
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "execution_job_reconciliation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    attempts_marked: int | None = None
    try:
        attempts_marked = await reconcile_stale_pending_execution_attempts()
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "execution_attempt_reconciliation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    docker_managed_enqueued: int | None = None
    try:
        docker_managed_enqueued = await reconcile_stale_docker_managed_attempts()
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "execution_attempt_docker_reconciliation_startup_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    logger.info(
        "execution_reconciliation_summary",
        stale_validating_jobs_marked=jobs_marked,
        stale_pending_create_attempts_marked=attempts_marked,
        stale_docker_managed_attempts_enqueued=docker_managed_enqueued,
    )


#: Persisted on every report asset this reconciles. Fixed and complete
#: -- no interpolation -- matching `STALE_RUN_FAILURE_REASON`'s own
#: scrubbing discipline: this string reaches `GET /reports/{asset_id}`
#: (auth-gated, but still user-facing on `processing_error`), so it
#: says what happened without saying anything about *why* the worker
#: died.
STALE_REPORT_FAILURE_REASON = (
    "Report generation marked failed during stale-report reconciliation after "
    "worker interruption."
)

#: The two `AssetType` values `ReportService.generate_synopsis` ever
#: creates (see `_KIND_ASSET_TYPE` there) -- the only asset types this
#: reconciler is scoped to.
_REPORT_ASSET_TYPES = (AssetType.REPORT, AssetType.SUMMARY)

#: `_generate_report` only ever leaves a report asset at one of these
#: two statuses without reaching a terminal state: `pending` if the
#: worker died before the task even started, `running` if it died mid
#: generation.
_STALE_REPORT_STATUSES = (AssetProcessingStatus.PENDING, AssetProcessingStatus.RUNNING)


async def reconcile_stale_report_generations() -> int:
    """Fail every GENERATED report `Asset` stuck at `pending`/`running`
    past the stale threshold. Returns the number of assets reconciled.

    Mirrors `reconcile_stale_research_runs`'s shape exactly (same
    "worker died mid-job, nothing else ever revisits the row" root
    cause, applied to `workers.tasks._generate_report` instead of
    `ResearchExecutionService.execute`): scoped to `source=GENERATED`
    report-kind assets (`REPORT`/`SUMMARY`) whose `updated_at` is older
    than `settings.report_generation_stale_after_seconds`. `updated_at`
    is the right clock here, not `created_at`: `_generate_report`
    commits the `RUNNING` transition immediately after creation, so a
    genuinely in-progress report always has a recent `updated_at`,
    exactly like `started_at` for a `ResearchRun`.

    Idempotent by construction, same as `reconcile_stale_research_runs`:
    the WHERE clause only matches rows still at `pending`/`running`, and
    this is the only function that ever moves a report asset out of
    those statuses without also completing it.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.report_generation_stale_after_seconds
    )

    async with async_session_factory() as session:
        result = await session.execute(
            select(Asset).where(
                Asset.source == AssetSource.GENERATED,
                Asset.asset_type.in_(_REPORT_ASSET_TYPES),
                Asset.processing_status.in_(_STALE_REPORT_STATUSES),
                Asset.updated_at < cutoff,
            )
        )
        stale_assets = list(result.scalars().all())

        for asset in stale_assets:
            asset.processing_status = AssetProcessingStatus.FAILED
            asset.processing_error = STALE_REPORT_FAILURE_REASON
            logger.warning(
                "report_generation_reconciled_stale",
                asset_id=str(asset.id),
                updated_at=asset.updated_at.isoformat() if asset.updated_at else None,
                stale_after_seconds=settings.report_generation_stale_after_seconds,
            )

        if stale_assets:
            await session.commit()

    return len(stale_assets)


async def reconcile_stale_report_generations_on_startup() -> None:
    """Entry point for the worker's `worker_ready` signal handler.

    Never raises -- a reconciliation failure must not prevent the
    worker from accepting new tasks, the same non-fatal contract
    `reconcile_stale_research_runs_on_startup` uses.
    """
    try:
        count = await reconcile_stale_report_generations()
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "report_generation_reconciliation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return

    logger.info("report_generation_reconciliation_summary", reconciled_count=count)


async def reconcile_stale_research_runs_on_startup() -> None:
    """Entry point for the worker's `worker_ready` signal handler.

    Never raises -- a reconciliation failure must not prevent the
    worker from accepting new tasks, the same non-fatal contract
    `run_startup_validation` uses on the API side.
    """
    try:
        count = await reconcile_stale_research_runs()
    except Exception as exc:  # noqa: BLE001 - startup path must not crash the worker
        logger.error(
            "research_run_reconciliation_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return

    logger.info("research_run_reconciliation_summary", reconciled_count=count)
