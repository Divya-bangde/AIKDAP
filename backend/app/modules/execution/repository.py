"""Data-access layer for the `ExecutionJob` model.

Contains only persistence operations, matching `TaskRepository`'s shape.
`get_by_id` is the operation the future launcher's Celery task body will
call with the `job_id` carried on its (minimal) Celery message (Phase
7B.7 Part 4/6) -- everything else about the job is re-fetched here, never
trusted from the message itself.

`claim_pending_job` (Phase 7B.11) is the one method here that commits its
own transaction rather than leaving that to the caller (unlike `create()`,
which only flushes) -- the atomic `PENDING -> VALIDATING` claim must be
durable and visible to concurrent transactions immediately, both to
resolve the row lock a competing claim attempt is blocked on and so a
subsequent guard/resolver failure in the same caller's session cannot
roll the claim back out from under a concurrent observer.
"""

import uuid
from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.execution.enums import ExecutionAttemptStatus, ExecutionJobStatus
from app.modules.execution.models import ExecutionAttempt, ExecutionJob


class ExecutionJobRepository:
    """Encapsulates all direct database access for `ExecutionJob` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, job_id: uuid.UUID) -> ExecutionJob | None:
        """Fetch an execution job by primary key, or None if not found."""
        return await self._session.get(ExecutionJob, job_id)

    async def list_by_owner(self, owner_id: uuid.UUID) -> list[ExecutionJob]:
        """Every job owned by `owner_id`, newest first (Sprint 16 Phase
        7B.23) -- backs `GET /execution/jobs`. Unfiltered by project or
        status: this slice's front door creates one job per call and
        nothing yet needs a narrower view."""
        result = await self._session.execute(
            select(ExecutionJob)
            .where(ExecutionJob.owner_id == owner_id)
            .order_by(ExecutionJob.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, job: ExecutionJob) -> ExecutionJob:
        """Insert a new execution job row and flush to populate generated fields."""
        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def claim_pending_job(self, job_id: uuid.UUID) -> ExecutionJob | None:
        """Atomically transitions a job `PENDING -> VALIDATING`.

        A single conditional `UPDATE ... WHERE id = :job_id AND status =
        'PENDING' RETURNING *` -- the database, not a read-then-write gap
        in Python, is the sole authority over which caller wins. Postgres
        resolves two concurrent attempts against the same row by making
        the second block on the row lock until the first's transaction
        ends, then re-evaluating the WHERE clause against the now-current
        (committed) state, so the loser always observes 0 matched rows
        rather than racing the winner.

        Returns the claimed row on success, or `None` if the job was not
        `PENDING` at the moment of the attempt -- lost the race, or was
        never eligible to begin with. Deliberately does not distinguish
        those two cases (or "job does not exist") itself; a caller that
        needs that distinction calls `get_by_id` separately.
        """
        stmt = (
            update(ExecutionJob)
            .where(ExecutionJob.id == job_id, ExecutionJob.status == ExecutionJobStatus.PENDING)
            .values(status=ExecutionJobStatus.VALIDATING)
            .returning(ExecutionJob)
        )
        result = await self._session.execute(stmt)
        claimed = result.scalar_one_or_none()
        if claimed is not None:
            await self._session.commit()
        return claimed

    async def claim_stale_validating_job_for_retry(
        self, job_id: uuid.UUID, cutoff: datetime
    ) -> ExecutionJob | None:
        """Atomically transitions a stale job `VALIDATING -> LAUNCHING`,
        authorizing exactly one retry-materialization attempt (Sprint 16
        Phase 7B.18, implemented 7B.21). Same shape and same commit
        contract as `claim_pending_job`, for the same reason: this claim
        must be durable and visible to a concurrent observer (a
        recovery worker's self-loop claim, `_claim_launching_for_retry`,
        checks `status`/`updated_at` on this exact row) before this
        caller does anything else -- including running the guard
        pipeline, whose rejection must not roll this claim back out from
        under a concurrent recovery attempt.

        The `updated_at < cutoff` condition (not present in
        `claim_pending_job`) restricts this to genuinely stale jobs --
        the caller decides staleness the same way `find_stale_validating_jobs`
        does, and passes the same cutoff here.
        """
        stmt = (
            update(ExecutionJob)
            .where(
                ExecutionJob.id == job_id,
                ExecutionJob.status == ExecutionJobStatus.VALIDATING,
                ExecutionJob.updated_at < cutoff,
            )
            .values(status=ExecutionJobStatus.LAUNCHING)
            .returning(ExecutionJob)
        )
        result = await self._session.execute(stmt)
        claimed = result.scalar_one_or_none()
        if claimed is not None:
            await self._session.commit()
        return claimed

    async def find_stale_launching_jobs(self, cutoff: datetime) -> list[ExecutionJob]:
        """Every job still at `LAUNCHING` whose `updated_at` is before
        `cutoff` -- detection only (Sprint 16 Phase 7B.19/7B.21). Mirrors
        `find_stale_validating_jobs` exactly. Staleness here is a
        pre-filter for "worth attempting recovery", not the safety
        mechanism -- `_claim_launching_for_recovery`'s atomic `NOT
        EXISTS` check is what actually prevents duplicate allocation,
        independent of this cutoff's precision.
        """
        result = await self._session.execute(
            select(ExecutionJob).where(
                ExecutionJob.status == ExecutionJobStatus.LAUNCHING,
                ExecutionJob.updated_at < cutoff,
            )
        )
        return list(result.scalars().all())

    async def _claim_launching_for_recovery(self, job_id: uuid.UUID) -> ExecutionJob | None:
        """PRIVATE -- Sprint 16 Phase 7B.20/7B.21's self-loop recovery
        claim. Calling this alone and committing separately reopens
        exactly the crash window Phase 7B.20 closed; it exists ONLY to
        be called from within `service.recover_interrupted_retry_attempt`,
        which immediately follows it with attempt-number allocation and
        `INSERT`, all committed together as one transaction.

        FLUSH ONLY -- never commits.

        THREE statements, deliberately NOT one atomic `UPDATE ...
        WHERE ... AND NOT EXISTS (...)`. An earlier version of this
        method used exactly that single-statement form and was PROVEN
        WRONG against real concurrent Postgres (Phase 7B.21): when a
        blocked `UPDATE` unblocks after the blocking transaction
        commits, Postgres's `EvalPlanQual` re-check refetches the
        TARGET ROW's latest version, but a correlated subquery against
        a DIFFERENT table (here, `execution_attempts`) embedded in that
        same statement is NOT guaranteed a fresh snapshot -- it can
        still evaluate against the pre-block view, incorrectly finding
        `NOT EXISTS` true even though the blocking transaction just
        committed the very attempt row that should have made it false.
        Reproduced deterministically (9/10 runs, then isolated and
        confirmed 3/3 with a minimal raw-SQL probe) before this fix;
        confirmed absent (5/5) after splitting into separate statements.

        The fix: (1) `SELECT ... FOR UPDATE` acquires the row lock --
        Postgres's own documented behavior for `FOR UPDATE` under
        `READ COMMITTED` is to block on a conflicting lock and, once
        acquired, return the row's LATEST committed version, not a
        stale one. (2) A SEPARATE, subsequent `SELECT EXISTS(...)`
        statement gets its own fresh `READ COMMITTED` snapshot --
        distinct from statement (1)'s snapshot -- and so correctly sees
        anything the just-unblocked transaction committed. (3) Only if
        both checks pass does the actual self-loop `UPDATE` run, still
        holding the SAME lock acquired in step 1 (never released
        between these three statements, since none of them commits or
        rolls back) -- so nothing can interleave between the checks and
        the write.
        """
        locked_result = await self._session.execute(
            select(ExecutionJob).where(ExecutionJob.id == job_id).with_for_update()
        )
        current = locked_result.scalar_one_or_none()
        if current is None or current.status is not ExecutionJobStatus.LAUNCHING:
            return None

        already_materialized_result = await self._session.execute(
            select(
                exists().where(
                    ExecutionAttempt.execution_job_id == job_id,
                    ExecutionAttempt.created_at >= current.updated_at,
                )
            )
        )
        if already_materialized_result.scalar():
            return None

        stmt = (
            update(ExecutionJob)
            .where(ExecutionJob.id == job_id, ExecutionJob.status == ExecutionJobStatus.LAUNCHING)
            .values(status=ExecutionJobStatus.LAUNCHING)  # self-loop; still bumps updated_at
            .returning(ExecutionJob)
        )
        result = await self._session.execute(stmt)
        claimed = result.scalar_one()
        await self._session.flush()
        return claimed

    async def request_cancellation(self, job_id: uuid.UUID) -> ExecutionJob | None:
        """Atomically transitions a job `RUNNING -> CANCEL_REQUESTED`
        (Sprint 16 Phase 7B.28) -- the SAME atomic conditional-`UPDATE`
        shape as `claim_pending_job`/`claim_stale_validating_job_for_retry`
        above, for the identical reason: the database, not a read-then-
        write gap in Python, must be the sole arbiter of whether a
        cancellation request is honored. Two concurrent callers racing
        the same job: exactly one matches this `WHERE status = 'RUNNING'`
        and wins; the other observes 0 rows (the job is already
        `CANCEL_REQUESTED`) and gets `None` back -- both return safely,
        neither raises, and no duplicate Docker operation results, since
        only the launcher's own polling loop (never a caller of this
        method) ever calls `container.kill()`.

        Returns `None` for "job does not exist", "job was never RUNNING",
        and "job already CANCEL_REQUESTED/terminal" alike -- deliberately
        not distinguished here, matching `claim_pending_job`'s own
        precedent; a caller needing the distinction calls `get_by_id`
        separately.
        """
        stmt = (
            update(ExecutionJob)
            .where(ExecutionJob.id == job_id, ExecutionJob.status == ExecutionJobStatus.RUNNING)
            .values(status=ExecutionJobStatus.CANCEL_REQUESTED)
            .returning(ExecutionJob)
        )
        result = await self._session.execute(stmt)
        claimed = result.scalar_one_or_none()
        if claimed is not None:
            await self._session.commit()
        return claimed

    async def finalize_running_job(
        self, job_id: uuid.UUID, *, status: ExecutionJobStatus, reason: str | None, completed_at: datetime
    ) -> ExecutionJob | None:
        """Atomically transitions a job out of `RUNNING`/`CANCEL_REQUESTED`/
        `VALIDATING` into a terminal status (Sprint 16 Phase 7B.29) -- the
        SAME atomic conditional-`UPDATE` shape as `claim_pending_job`/
        `request_cancellation` above, extended to cover every status a
        job whose Docker-managed attempt needs finalizing can genuinely be
        found in.

        `VALIDATING` is included alongside `RUNNING`/`CANCEL_REQUESTED`
        for one specific, real reason: `execute_approved_launch` does NOT
        write `job.status = RUNNING` until AFTER `docker start()` succeeds
        -- so a crash between `docker create()` and `docker start()`
        (Sprint 16 Phase 7B.29's Case 1: an `ExecutionAttempt` stuck at
        `CREATED` with a real, never-started Docker container) leaves the
        JOB at `VALIDATING`, not `RUNNING`, at the moment a reconciler
        finds it. Safe to include: the only other code that ever writes to
        a `VALIDATING` job's `status` is `claim_stale_validating_job_for_
        retry` (`VALIDATING -> LAUNCHING`), and nothing in this
        repository's callers currently invokes it (`prepare_retry_attempt`
        has no caller anywhere in this codebase -- confirmed by inspection,
        Sprint 16 Phase 7B.29) -- there is no live path that could race
        this transition out from under a `VALIDATING` job today.
        `reconcile_stale_validating_execution_jobs` only ever writes
        `job.reason`, never `status`, so it cannot conflict either.

        This is the single serialization point that makes Docker-aware
        reconciliation safe to run concurrently with a still-live launcher,
        or with another reconciler processing the same attempt (Sprint 16
        Phase 7B.29 races A/B/D): whichever caller's `UPDATE ... WHERE
        status IN (...)` matches first wins and gets the claimed row back;
        every other concurrent caller's WHERE clause no longer matches
        (the job already moved to a terminal status) and gets `None` --
        both return safely, and `execution_launcher.launcher` treats only
        the WINNER as authorized to create a result `Asset` for this
        attempt, which is what actually prevents a duplicate (see
        `_finalize_exited_attempt`'s own docstring).

        Returns `None` for "job does not exist", "job was never in one of
        these statuses", and "already terminal" alike -- deliberately not
        distinguished here, matching `claim_pending_job`'s own precedent.
        """
        stmt = (
            update(ExecutionJob)
            .where(
                ExecutionJob.id == job_id,
                ExecutionJob.status.in_(
                    [
                        ExecutionJobStatus.RUNNING,
                        ExecutionJobStatus.CANCEL_REQUESTED,
                        ExecutionJobStatus.VALIDATING,
                    ]
                ),
            )
            .values(status=status, reason=reason, completed_at=completed_at)
            .returning(ExecutionJob)
        )
        result = await self._session.execute(stmt)
        claimed = result.scalar_one_or_none()
        if claimed is not None:
            await self._session.commit()
        return claimed

    async def find_stale_validating_jobs(self, cutoff: datetime) -> list[ExecutionJob]:
        """Every job still at `VALIDATING` whose `updated_at` is before
        `cutoff`. A read-only query -- the staleness *policy* lives in
        `app.workers.reconciliation`, not here.

        `updated_at`, not `created_at`: `created_at` marks job creation,
        which can predate the `PENDING -> VALIDATING` claim by an
        arbitrary, unrelated amount of time (a job can sit `PENDING` in
        a queue) -- using it would false-positive on a job that only
        just entered `VALIDATING`. `updated_at` is not a guess here: it
        is empirically confirmed (see Phase 7B.16 report) that
        `claim_pending_job()`'s bulk `UPDATE` fires this column's
        `onupdate=func.now()`, and nothing else in the current
        architecture ever writes to an `ExecutionJob` row while it sits
        at `VALIDATING` -- so, for any row this query can find, the
        value is exactly the claim timestamp, not an approximation.

        This does mean a caller that later writes this same row (e.g.
        this module's own reconciliation, recording a diagnostic) moves
        `updated_at` again and the row stops meaning "time of entering
        VALIDATING" -- documented, not hidden; see
        `reconcile_stale_validating_execution_jobs`'s docstring for why
        that is an accepted, harmless consequence rather than a bug.

        Deliberately narrow: every other `ExecutionJobStatus` is excluded
        by status regardless of age -- a job's outcome once it leaves
        `VALIDATING` is not this query's business. Deliberately unscoped
        by project/owner, matching the attempt-level query.
        """
        result = await self._session.execute(
            select(ExecutionJob).where(
                ExecutionJob.status == ExecutionJobStatus.VALIDATING,
                ExecutionJob.updated_at < cutoff,
            )
        )
        return list(result.scalars().all())


class ExecutionAttemptRepository:
    """Encapsulates all direct database access for `ExecutionAttempt`
    rows. Persistence only (Phase 7B.13) -- no `launch()`, `retry()`,
    `reconcile()`, `cancel()`, `kill()`, `start()`, or `stop()` belongs
    here; those are future-slice launcher/reconciler behavior, not data
    access."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, attempt_id: uuid.UUID) -> ExecutionAttempt | None:
        """Fetch an execution attempt by primary key, or None if not found."""
        return await self._session.get(ExecutionAttempt, attempt_id)

    async def list_by_job(self, job_id: uuid.UUID) -> list[ExecutionAttempt]:
        """Every attempt for one job, ordered by `attempt_number` (Sprint
        16 Phase 7B.23) -- backs `GET /execution/jobs/{job_id}`."""
        result = await self._session.execute(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.execution_job_id == job_id)
            .order_by(ExecutionAttempt.attempt_number)
        )
        return list(result.scalars().all())

    async def create(self, attempt: ExecutionAttempt) -> ExecutionAttempt:
        """Insert a new execution attempt row and flush to populate
        generated fields -- matches `ExecutionJobRepository.create()` and
        `ResearchStepRepository.create()`: no commit here, the caller
        controls the transaction boundary."""
        self._session.add(attempt)
        await self._session.flush()
        await self._session.refresh(attempt)
        return attempt

    async def find_stale_pending_attempts(self, cutoff: datetime) -> list[ExecutionAttempt]:
        """Every attempt still at `PENDING_CREATE` that was created before
        `cutoff`. A read-only query -- the staleness *policy* (how the
        cutoff is derived, and what is recorded about the rows it finds)
        belongs to `app.workers.reconciliation`, not here.

        `created_at` is the timestamp, not `started_at`: a `PENDING_CREATE`
        attempt by definition predates any Docker call, and nothing writes
        `started_at` until a future slice confirms a container actually
        started -- so `started_at` is always NULL for exactly the rows this
        query exists to find. `updated_at` would be wrong for a different
        reason: reconciliation writing to the row would push it forward and
        make the row look freshly active.

        Deliberately narrow: `CREATED`/`RUNNING`/`EXITED`/`UNKNOWN` rows are
        excluded by status regardless of age, because a container's own
        lifecycle is not this query's business. Deliberately unscoped by
        project/owner, matching `reconcile_stale_research_runs` -- this is
        infrastructure recovery over the whole deployment, never a
        user-facing, tenant-scoped read.
        """
        result = await self._session.execute(
            select(ExecutionAttempt).where(
                ExecutionAttempt.status == ExecutionAttemptStatus.PENDING_CREATE,
                ExecutionAttempt.created_at < cutoff,
            )
        )
        return list(result.scalars().all())

    async def find_stale_active_attempts(self, cutoff: datetime) -> list[ExecutionAttempt]:
        """Every attempt still at `CREATED` or `RUNNING` whose `updated_at`
        is before `cutoff` (Sprint 16 Phase 7B.29) -- detection only, the
        Docker-aware counterpart of `find_stale_pending_attempts`.

        `updated_at`, not `created_at`: both `CREATED` and `RUNNING` are
        written by `execution_launcher.launcher._run_started_container`/
        `execute_approved_launch` exactly once each, at the moment Docker
        itself confirms the corresponding transition -- so `updated_at` on
        a matching row means "time this attempt was last known to make
        real progress," not merely "time the row was inserted."

        Reuses `execution_attempt_stale_after_seconds` rather than a new
        setting: every current resource class's `timeout_s` is at most 10
        seconds (`docker_policy._RESOURCE_CLASS_CONFIG`), so a genuinely
        healthy attempt always leaves `CREATED`/`RUNNING` within roughly
        that long; the existing 300-second ceiling (already sized as a
        conservative worst case for the *slower*, pre-Docker-call
        `PENDING_CREATE` span) is a wide, safe margin here too, not a
        borrowed value that happens to fit by coincidence.

        Deliberately narrow: `PENDING_CREATE` (covered by
        `find_stale_pending_attempts`) and `EXITED`/`UNKNOWN` (already
        terminal -- nothing to detect) are excluded by status regardless of
        age.
        """
        result = await self._session.execute(
            select(ExecutionAttempt).where(
                ExecutionAttempt.status.in_(
                    [ExecutionAttemptStatus.CREATED, ExecutionAttemptStatus.RUNNING]
                ),
                ExecutionAttempt.updated_at < cutoff,
            )
        )
        return list(result.scalars().all())
