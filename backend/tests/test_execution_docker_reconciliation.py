"""Sprint 16 Phase 7B.29 -- Docker-aware crash recovery & reconciliation
(T1-T20).

Every test here runs against REAL Postgres and the REAL Docker daemon.
Mocks appear only in T11 (container removed between `get()` and
`.reload()` -- timing-dependent to the point of being impractical to
force deterministically through real daemon scheduling) and T18/T19
(a genuine Docker daemon/API failure during termination/connection),
matching the phase's own "narrow control-plane cases impractical
against a real daemon" exception -- the same boundary
`test_execution_timeout_cancellation.py`'s T12 already established.

Uses `alpine:3.19` directly (bypassing the guard) for every hand-built
scenario, exactly like `test_execution_real_docker_lifecycle.py`'s and
`test_execution_timeout_cancellation.py`'s own `_spec()` helpers -- the
production CLASS_EXPR dispatcher image is untouched by this phase. T20
is the one exception: it drives the REAL production front door
(`ExperimentPlanService` -> `prepare_approved_launch`) to prove
reconciliation recovers a genuinely production-shaped execution.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import docker
import docker.errors
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database.session import async_session_factory, engine
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.auth.models import User
from app.modules.execution.enums import ExecutionAttemptStatus, ExecutionJobStatus
from app.modules.execution.models import ExecutionAttempt, ExecutionJob
from app.modules.execution.repository import ExecutionAttemptRepository, ExecutionJobRepository
from app.modules.execution.service import (
    container_name_for_attempt,
    prepare_approved_launch,
    request_execution_cancellation,
)
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from app.modules.research.experiment_schemas import ExperimentPlanCreateFromEquation
from app.modules.research.experiment_service import ExperimentPlanService
from execution_launcher.docker_policy import resource_class_timeout_s
from execution_launcher.launcher import (
    _docker_create_kwargs,
    execute_approved_launch,
    reconcile_attempt,
)
from execution_launcher.models import ApprovedLaunchSpec, ExecutionCapability, ExecutionOperation, ResourceClass

CLASS_EXPR_TIMEOUT_S = resource_class_timeout_s(ResourceClass.CLASS_EXPR)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    await engine.dispose()
    yield


@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()


async def _make_owner_and_project(session, *, name: str) -> Project:
    user = User(
        email=f"pytest-reconcile-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Docker Reconciliation Test User",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    project = Project(
        owner_id=user.id,
        name=name,
        description="Created by test_execution_docker_reconciliation.py",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def project(session) -> AsyncIterator[Project]:
    proj = await _make_owner_and_project(session, name="Docker Reconciliation Test Project")
    yield proj
    user = await session.get(User, proj.owner_id)
    if user is not None:
        await session.delete(user)
        await session.commit()


async def _make_job_and_attempt(
    session, project: Project, *, job_status: ExecutionJobStatus = ExecutionJobStatus.VALIDATING
) -> tuple[ExecutionJob, ExecutionAttempt]:
    job = ExecutionJob(
        project_id=project.id,
        owner_id=project.owner_id,
        experiment_plan_id=uuid.uuid4(),
        experiment_plan_version=1,
        capability=ExecutionCapability.EVALUATE_EXPRESSION,
        operation=ExecutionOperation.EVALUATE_EXPRESSION,
        resource_class=ResourceClass.CLASS_EXPR,
        parameters={"expression": "1+1"},
        input_asset_ids=[],
        status=job_status,
    )
    session.add(job)
    await session.flush()

    attempt_id = uuid.uuid4()
    attempt = ExecutionAttempt(
        id=attempt_id,
        execution_job_id=job.id,
        attempt_number=1,
        container_name=container_name_for_attempt(attempt_id),
    )
    session.add(attempt)
    await session.flush()
    await session.commit()
    await session.refresh(job)
    await session.refresh(attempt)
    return job, attempt


def _spec(job: ExecutionJob, *, command: tuple[str, ...], timeout_s: float = CLASS_EXPR_TIMEOUT_S) -> ApprovedLaunchSpec:
    """Same fixed posture as the sibling real-Docker test files'
    `_spec()` helpers, duplicated locally per this repo's established
    per-file self-containment convention."""
    return ApprovedLaunchSpec(
        job_id=job.id,
        image="alpine:3.19",
        network_mode="none",
        privileged=False,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges",),
        read_only=True,
        user="10001:10001",
        mem_limit_mb=256,
        memswap_limit_mb=256,
        cpus=1.0,
        pids_limit=32,
        tmpfs_mb=64,
        mounts=(),
        environment={},
        command=command,
        timeout_s=timeout_s,
    )


async def _reload_attempt(attempt_id: uuid.UUID) -> ExecutionAttempt:
    async with async_session_factory() as verify_session:
        attempt = await verify_session.get(ExecutionAttempt, attempt_id)
        assert attempt is not None
        return attempt


async def _reload_job(job_id: uuid.UUID) -> ExecutionJob:
    async with async_session_factory() as verify_session:
        job = await verify_session.get(ExecutionJob, job_id)
        assert job is not None
        return job


async def _count_assets_for_attempt(attempt_id: uuid.UUID) -> int:
    async with async_session_factory() as verify_session:
        result = await verify_session.execute(
            select(Asset).where(Asset.asset_metadata["execution_attempt_id"].astext == str(attempt_id))
        )
        return len(result.scalars().all())


def _assert_container_gone(docker_client, name: str) -> None:
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(name)


async def _reconcile(attempt_id: uuid.UUID):
    async with async_session_factory() as session:
        return await reconcile_attempt(attempt_id, session)


def _create_real_container(docker_client, spec: ApprovedLaunchSpec, *, container_name: str):
    kwargs = _docker_create_kwargs(spec, container_name=container_name)
    return docker_client.containers.create(**kwargs)


async def _mark_created(session, attempt: ExecutionAttempt, container_id: str) -> None:
    attempt.container_id = container_id
    attempt.status = ExecutionAttemptStatus.CREATED
    await session.commit()
    await session.refresh(attempt)


async def _mark_running(session, job: ExecutionJob, attempt: ExecutionAttempt, container_id: str, started_at: datetime) -> None:
    attempt.container_id = container_id
    attempt.status = ExecutionAttemptStatus.RUNNING
    attempt.started_at = started_at
    job.status = ExecutionJobStatus.RUNNING
    job.started_at = started_at
    await session.commit()
    await session.refresh(job)
    await session.refresh(attempt)


# ---------------------------------------------------------------------------
# T1 -- CREATED attempt + real container CREATED + worker disappears.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t1_created_attempt_container_created_recovered_as_abandoned(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "2"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    try:
        await _mark_created(session, attempt, container.id)

        outcome = await reconcile_attempt(attempt.id, session)

        assert outcome.action == "recovered_created_abandoned"
        reloaded_attempt = await _reload_attempt(attempt.id)
        assert reloaded_attempt.status == ExecutionAttemptStatus.UNKNOWN
        reloaded_job = await _reload_job(job.id)
        assert reloaded_job.status == ExecutionJobStatus.FAILED
        assert reloaded_job.completed_at is not None
        _assert_container_gone(docker_client, attempt.container_name)
    finally:
        try:
            docker_client.containers.get(attempt.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass


# ---------------------------------------------------------------------------
# T2 -- RUNNING attempt + real container RUNNING + worker disappears; the
# container is still healthy and finishes naturally within budget.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t2_running_attempt_container_running_recovers_naturally(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "sleep 1; echo hello-t2; exit 0"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    container.start()
    container.reload()
    started_at = datetime.now(timezone.utc)
    await _mark_running(session, job, attempt, container.id, started_at)

    outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "recovered_running"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.EXITED
    assert reloaded_attempt.exit_code == 0
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    assert outcome.result_asset_id is not None
    asset = await AssetRepository(session).get_by_id(outcome.result_asset_id)
    assert "hello-t2" in asset.asset_metadata["stdout"]
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T3 -- RUNNING attempt + container naturally EXITS (0) before reconciliation.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t3_running_attempt_container_already_exited_zero(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo done-t3; exit 0"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    container.start()
    container.wait()  # real, unbounded is fine here -- the command finishes in well under a second
    started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _mark_running(session, job, attempt, container.id, started_at)

    outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "recovered_exited"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.EXITED
    assert reloaded_attempt.exit_code == 0
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    asset = await AssetRepository(session).get_by_id(outcome.result_asset_id)
    assert "done-t3" in asset.asset_metadata["stdout"]
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T4 -- RUNNING attempt + container exits non-zero.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t4_running_attempt_container_already_exited_nonzero(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo boom-t4 1>&2; exit 9"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    container.start()
    container.wait()
    started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _mark_running(session, job, attempt, container.id, started_at)

    outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "recovered_exited"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.exit_code == 9
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.FAILED
    asset = await AssetRepository(session).get_by_id(outcome.result_asset_id)
    assert "boom-t4" in asset.asset_metadata["stderr"]
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T5 -- RUNNING attempt + container does not exist (genuinely lost).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_running_attempt_container_missing_marked_unknown(session, project):
    job, attempt = await _make_job_and_attempt(session, project)
    attempt.container_id = "0" * 64  # a syntactically-plausible id Docker has never seen
    attempt.status = ExecutionAttemptStatus.RUNNING
    attempt.started_at = datetime.now(timezone.utc)
    job.status = ExecutionJobStatus.RUNNING
    job.started_at = attempt.started_at
    await session.commit()

    outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "lost_marked_unknown"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.UNKNOWN
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.FAILED
    assert reloaded_job.reason is not None and "could not be determined" in reloaded_job.reason


# ---------------------------------------------------------------------------
# T6 -- CREATED attempt + container does not exist.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_created_attempt_container_missing_marked_unknown(session, project):
    job, attempt = await _make_job_and_attempt(session, project)
    attempt.container_id = "1" * 64
    attempt.status = ExecutionAttemptStatus.CREATED
    await session.commit()

    outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "lost_marked_unknown"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.UNKNOWN
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.FAILED


# ---------------------------------------------------------------------------
# T7 -- EXITED attempt + container still exists -> cleanup only.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t7_exited_attempt_leftover_container_cleaned_up_only(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("true",))
    # Patched no-op cleanup -- simulates a crash strictly between
    # `_finalize_exited_attempt`'s terminal write and `execute_approved_
    # launch`'s own `_best_effort_remove` call, leaving the SAME
    # container_id/container still present (not a fresh, differently-ID'd
    # container, which would not be a faithful simulation of this crash
    # window).
    with patch("execution_launcher.launcher._best_effort_remove", return_value=None):
        outcome1 = await execute_approved_launch(
            spec,
            attempt_id=attempt.id,
            container_name=attempt.container_name,
            project_id=project.id,
            owner_id=project.owner_id,
            job_id=job.id,
            session=session,
        )
    try:
        outcome2 = await reconcile_attempt(attempt.id, session)

        assert outcome1.succeeded is True
        assert outcome2.action == "already_terminal_cleaned_up"
        reloaded_job = await _reload_job(job.id)
        assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED  # untouched
        assert await _count_assets_for_attempt(attempt.id) == 1  # no duplicate
        _assert_container_gone(docker_client, attempt.container_name)
    finally:
        try:
            docker_client.containers.get(attempt.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass


# ---------------------------------------------------------------------------
# T8 -- EXITED attempt + container already gone -> safe no-op.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t8_exited_attempt_container_already_gone_noop(session, project):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("true",))
    await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )  # already cleans up its own container

    outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "already_terminal_no_container"
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    assert await _count_assets_for_attempt(attempt.id) == 1


# ---------------------------------------------------------------------------
# T9 -- two reconciliation workers process the same attempt concurrently.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t9_two_concurrent_reconcilers_one_terminal_outcome(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo hello-t9; exit 0"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    container.start()
    container.wait()
    started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _mark_running(session, job, attempt, container.id, started_at)

    outcome_a, outcome_b = await asyncio.gather(
        _reconcile(attempt.id), _reconcile(attempt.id), return_exceptions=True
    )

    assert not isinstance(outcome_a, Exception), outcome_a
    assert not isinstance(outcome_b, Exception), outcome_b
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    assert await _count_assets_for_attempt(attempt.id) == 1, "no duplicate result Asset"
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T10 -- the live launcher and a reconciler race on the same attempt.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t10_launcher_and_reconciler_race_one_terminal_outcome(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "sleep 1; echo hello-t10; exit 0"))

    async def _live_launch():
        async with async_session_factory() as own_session:
            return await execute_approved_launch(
                spec,
                attempt_id=attempt.id,
                container_name=attempt.container_name,
                project_id=project.id,
                owner_id=project.owner_id,
                job_id=job.id,
                session=own_session,
            )

    async def _delayed_reconcile():
        await asyncio.sleep(0.3)  # let the launcher create+start first
        return await _reconcile(attempt.id)

    launch_outcome, reconcile_outcome = await asyncio.gather(
        _live_launch(), _delayed_reconcile(), return_exceptions=True
    )

    assert not isinstance(launch_outcome, Exception), launch_outcome
    assert not isinstance(reconcile_outcome, Exception), reconcile_outcome
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status in (ExecutionJobStatus.SUCCEEDED, ExecutionJobStatus.FAILED)
    assert await _count_assets_for_attempt(attempt.id) == 1
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T11 -- container removed between get() and inspect(). Timing-dependent
# to the point of being impractical to force through real daemon
# scheduling deterministically -- MOCKED, narrowly, matching the phase's
# own exception (the same boundary Phase 7B.28's T12 already established).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t11_container_removed_between_get_and_inspect(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "2"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    await _mark_created(session, attempt, container.id)

    with patch(
        "docker.models.containers.Container.reload",
        side_effect=docker.errors.NotFound("removed between get() and inspect()"),
    ):
        outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "lost_marked_unknown"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.UNKNOWN

    try:
        docker_client.containers.get(attempt.container_name).remove(force=True)
    except docker.errors.NotFound:
        pass


# ---------------------------------------------------------------------------
# T12/T13/T14 -- already-terminal SUCCEEDED/CANCELLED/TIMED_OUT job with a
# leftover container -> cleanup only, terminal status never reclassified.
# ---------------------------------------------------------------------------


async def _finalized_attempt_with_leftover_container(session, project, docker_client, *, target_status: ExecutionJobStatus):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "2"))
    # The leftover MUST be the same real container the (simulated) launcher
    # already recorded container_id for -- a differently-ID'd container
    # sharing only the name would make the identity lookup (container_id
    # first) miss it entirely, which is not what "cleanup was incomplete"
    # means.
    leftover = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    attempt.container_id = leftover.id
    attempt.status = ExecutionAttemptStatus.EXITED
    attempt.exit_code = 0
    attempt.started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    attempt.completed_at = datetime.now(timezone.utc)
    job.status = target_status
    job.completed_at = attempt.completed_at
    await session.commit()
    return job, attempt, leftover


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t12_succeeded_job_leftover_container_cleaned_up(session, project, docker_client):
    job, attempt, leftover = await _finalized_attempt_with_leftover_container(
        session, project, docker_client, target_status=ExecutionJobStatus.SUCCEEDED
    )
    outcome = await reconcile_attempt(attempt.id, session)
    assert outcome.action == "already_terminal_cleaned_up"
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    _assert_container_gone(docker_client, attempt.container_name)


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t13_cancelled_job_leftover_container_cleaned_up(session, project, docker_client):
    job, attempt, leftover = await _finalized_attempt_with_leftover_container(
        session, project, docker_client, target_status=ExecutionJobStatus.CANCELLED
    )
    outcome = await reconcile_attempt(attempt.id, session)
    assert outcome.action == "already_terminal_cleaned_up"
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.CANCELLED
    _assert_container_gone(docker_client, attempt.container_name)


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t14_timed_out_job_leftover_container_cleaned_up(session, project, docker_client):
    job, attempt, leftover = await _finalized_attempt_with_leftover_container(
        session, project, docker_client, target_status=ExecutionJobStatus.TIMED_OUT
    )
    outcome = await reconcile_attempt(attempt.id, session)
    assert outcome.action == "already_terminal_cleaned_up"
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.TIMED_OUT
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T15 -- repeated reconciliation is idempotent.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t15_repeated_reconciliation_idempotent(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo hello-t15; exit 0"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    container.start()
    container.wait()
    started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _mark_running(session, job, attempt, container.id, started_at)

    outcome1 = await reconcile_attempt(attempt.id, session)
    outcome2 = await reconcile_attempt(attempt.id, session)
    outcome3 = await reconcile_attempt(attempt.id, session)

    assert outcome1.action == "recovered_exited"
    assert outcome2.action == "already_terminal_no_container"
    assert outcome3.action == "already_terminal_no_container"
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    assert await _count_assets_for_attempt(attempt.id) == 1  # T16: no duplicate across 3 runs


# ---------------------------------------------------------------------------
# T17 -- cleanup NotFound is safe (already covered structurally by T8/T15's
# second call; this test isolates it explicitly).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t17_cleanup_of_already_removed_container_is_safe(session, project):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("true",))
    await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    # Second and third pass: no NotFound propagates, no exception raised.
    outcome_a = await reconcile_attempt(attempt.id, session)
    outcome_b = await reconcile_attempt(attempt.id, session)
    assert outcome_a.action == "already_terminal_no_container"
    assert outcome_b.action == "already_terminal_no_container"


# ---------------------------------------------------------------------------
# T18 -- cleanup failure preserves the primary (recovered) result.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t18_cleanup_failure_preserves_recovered_result(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo hello-t18; exit 0"))
    container = _create_real_container(docker_client, spec, container_name=attempt.container_name)
    container.start()
    container.wait()
    started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _mark_running(session, job, attempt, container.id, started_at)

    with patch(
        "docker.models.containers.Container.remove",
        side_effect=docker.errors.APIError("simulated cleanup failure"),
    ):
        outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "recovered_exited"
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED, "cleanup failure must not flip a real success"
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.EXITED
    assert outcome.result_asset_id is not None

    docker_client.containers.get(attempt.container_name).remove(force=True)  # real cleanup for the suite


# ---------------------------------------------------------------------------
# T19 -- Docker daemon unreachable preserves truthful (unchanged) state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t19_daemon_unreachable_preserves_state_no_false_write(session, project):
    job, attempt = await _make_job_and_attempt(session, project)
    attempt.status = ExecutionAttemptStatus.RUNNING
    attempt.container_id = "3" * 64
    attempt.started_at = datetime.now(timezone.utc)
    job.status = ExecutionJobStatus.RUNNING
    job.started_at = attempt.started_at
    await session.commit()

    with patch("docker.from_env", side_effect=docker.errors.DockerException("daemon unreachable")):
        outcome = await reconcile_attempt(attempt.id, session)

    assert outcome.action == "daemon_unreachable_deferred"
    reloaded_attempt = await _reload_attempt(attempt.id)
    reloaded_job = await _reload_job(job.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.RUNNING, "no write on daemon failure"
    assert reloaded_job.status == ExecutionJobStatus.RUNNING, "no write on daemon failure"


# ---------------------------------------------------------------------------
# T20 -- real front door, a genuinely separate OS process simulates the
# worker crash, reconciliation recovers the final persisted result.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
def test_t20_real_front_door_crash_and_reconcile_recovers_14(docker_client):
    async def _setup():
        async with async_session_factory() as setup_session:
            proj = await _make_owner_and_project(setup_session, name="T20 Front Door Project")
            service = ExperimentPlanService(setup_session)
            plan = await service.create_from_equation(
                proj.owner_id,
                ExperimentPlanCreateFromEquation(
                    project_id=proj.id, title="7B.29 T20 real front door", expression="2 + 3 * 4"
                ),
            )
            job = await service.request_execution(proj.owner_id, plan.id, "baseline")
            preparation = await prepare_approved_launch(job.id, setup_session)
            return proj, job, preparation

    project_, job_, preparation_ = asyncio.run(_setup())
    approved = preparation_.approved_spec

    # A genuinely separate OS process performs the REAL docker create() +
    # start() + durable persistence a launcher would, then exits WITHOUT
    # ever calling wait()/finalizing -- the real crash window this phase
    # exists to recover from (same method as
    # test_execution_real_docker_lifecycle.py's T6/T7: a real subprocess,
    # not a faked early return).
    script = (
        "import asyncio, uuid\n"
        "from datetime import datetime, timezone\n"
        "import app.workers.celery_app\n"  # registers every ORM model on Base.metadata (users/projects FKs)
        "from app.database.session import async_session_factory\n"
        "from app.modules.execution.repository import ExecutionAttemptRepository, ExecutionJobRepository\n"
        "from app.modules.execution.enums import ExecutionAttemptStatus, ExecutionJobStatus\n"
        "from execution_launcher.launcher import _docker_create_kwargs\n"
        "from execution_launcher.models import ApprovedLaunchSpec\n"
        "import docker\n"
        "async def main():\n"
        f"    attempt_id = uuid.UUID('{preparation_.attempt_id}')\n"
        f"    job_id = uuid.UUID('{job_.id}')\n"
        "    async with async_session_factory() as session:\n"
        "        attempts = ExecutionAttemptRepository(session)\n"
        "        jobs = ExecutionJobRepository(session)\n"
        "        attempt = await attempts.get_by_id(attempt_id)\n"
        "        job = await jobs.get_by_id(job_id)\n"
        f"        approved = ApprovedLaunchSpec(\n"
        f"            job_id=job_id, image={approved.image!r}, network_mode={approved.network_mode!r},\n"
        f"            privileged={approved.privileged!r}, cap_drop={approved.cap_drop!r},\n"
        f"            security_opt={approved.security_opt!r}, read_only={approved.read_only!r},\n"
        f"            user={approved.user!r}, mem_limit_mb={approved.mem_limit_mb!r},\n"
        f"            memswap_limit_mb={approved.memswap_limit_mb!r}, cpus={approved.cpus!r},\n"
        f"            pids_limit={approved.pids_limit!r}, tmpfs_mb={approved.tmpfs_mb!r}, mounts=(),\n"
        f"            environment={approved.environment!r}, command={approved.command!r},\n"
        f"            timeout_s={approved.timeout_s!r},\n"
        "        )\n"
        "        kwargs = _docker_create_kwargs(approved, container_name=attempt.container_name)\n"
        "        client = docker.from_env()\n"
        "        container = client.containers.create(**kwargs)\n"
        "        attempt.container_id = container.id\n"
        "        attempt.status = ExecutionAttemptStatus.CREATED\n"
        "        await session.commit()\n"
        "        container.start()\n"
        "        container.reload()\n"
        "        attempt.status = ExecutionAttemptStatus.RUNNING\n"
        "        attempt.started_at = datetime.now(timezone.utc)\n"
        "        job.status = ExecutionJobStatus.RUNNING\n"
        "        job.started_at = attempt.started_at\n"
        "        await session.commit()\n"
        "        print(container.id)\n"
        "    # Deliberately exits here -- no wait(), no finalize, simulating\n"
        "    # a hard crash mid-execution.\n"
        "asyncio.run(main())\n"
    )
    # This process's own connection pool must not carry a connection bound
    # to `_setup()`'s now-closed event loop into the NEXT `asyncio.run()`
    # call below -- the exact "asyncpg connection attached to a different
    # loop" trap `app.workers.worker`'s own docstring documents (Sprint 16
    # Phase 7B.29: discovered live while writing this test).
    asyncio.run(engine.dispose())

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=os.path.dirname(os.path.dirname(__file__)) or ".",
    )
    assert result.returncode == 0, result.stderr[-3000:]
    persisted_container_id = result.stdout.strip().splitlines()[-1]

    try:
        # Confirm the crash was real and observable by THIS separate
        # process before reconciling.
        observed = docker_client.containers.get(preparation_.container_name)
        observed.reload()
        assert observed.id == persisted_container_id
        assert observed.attrs["State"]["Status"] in ("running", "exited")

        async def _reconcile_and_verify():
            async with async_session_factory() as recon_session:
                outcome = await reconcile_attempt(preparation_.attempt_id, recon_session)
                assert outcome.action in ("recovered_running", "recovered_exited")

            async with async_session_factory() as verify_session:
                job = await ExecutionJobRepository(verify_session).get_by_id(job_.id)
                assert job.status == ExecutionJobStatus.SUCCEEDED

                attempt = await ExecutionAttemptRepository(verify_session).get_by_id(preparation_.attempt_id)
                assert attempt.status == ExecutionAttemptStatus.EXITED
                assert attempt.exit_code == 0

                asset = await AssetRepository(verify_session).find_by_execution_attempt(preparation_.attempt_id)
                assert asset is not None
                import json

                stdout = json.loads(asset.asset_metadata["stdout"])
                assert stdout == {"result": 14.0, "error": None}

        asyncio.run(_reconcile_and_verify())
    finally:
        try:
            docker_client.containers.get(preparation_.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass

        asyncio.run(engine.dispose())

        async def _teardown():
            async with async_session_factory() as teardown_session:
                user = await teardown_session.get(User, project_.owner_id)
                if user is not None:
                    await teardown_session.delete(user)
                    await teardown_session.commit()

        asyncio.run(_teardown())


# ---------------------------------------------------------------------------
# Structural -- request_execution_cancellation still exists and is unused
# by reconciliation (Sprint 16 Phase 7B.28 surface untouched by this phase).
# ---------------------------------------------------------------------------


def test_request_execution_cancellation_import_unaffected():
    assert callable(request_execution_cancellation)
