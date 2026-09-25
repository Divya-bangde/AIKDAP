"""Sprint 16 Phase 7B.28 -- real Docker timeout & cancellation (T1-T15).

Every test here runs against REAL Postgres and the REAL Docker daemon
(mocks appear only in T12, the one narrow control-plane case the phase
itself calls out as impractical to force through a real daemon
deterministically -- a genuine daemon/API failure mid-termination).

Uses `alpine:3.19` with a `sleep`-based command directly (bypassing the
guard, exactly like `test_execution_real_docker_lifecycle.py`'s own
`_spec()` pattern) for the long-running timeout workload -- the
production CLASS_EXPR dispatcher image is untouched by this phase and
never runs anything long-lived.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
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
from app.modules.execution.repository import ExecutionJobRepository
from app.modules.execution.service import container_name_for_attempt, request_execution_cancellation
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from app.modules.research.experiment_schemas import ExperimentPlanCreateFromEquation
from app.modules.research.experiment_service import ExperimentPlanService
from app.workers.tasks import launch_execution_job
from execution_launcher import docker_policy
from execution_launcher.docker_policy import validate_and_approve
from execution_launcher.launcher import execute_approved_launch
from execution_launcher.models import (
    ApprovedLaunchSpec,
    ExecutionCapability,
    ExecutionOperation,
    ResourceClass,
)
from execution_launcher.resolvers import StaticInputResolver
from execution_launcher.translator import build_candidate

REAL_IMAGE_DIGEST = docker_policy._APPROVED_IMAGE_DIGESTS[ResourceClass.CLASS_EXPR]


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    await engine.dispose()
    yield


@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()


async def _make_owner_and_project(session, *, name: str) -> Project:
    user = User(
        email=f"pytest-timeout-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Timeout/Cancellation Test User",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    project = Project(
        owner_id=user.id,
        name=name,
        description="Created by test_execution_timeout_cancellation.py",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def project(session) -> AsyncIterator[Project]:
    proj = await _make_owner_and_project(session, name="Timeout/Cancellation Test Project")
    yield proj
    user = await session.get(User, proj.owner_id)
    if user is not None:
        await session.delete(user)
        await session.commit()


async def _make_job_and_attempt(session, project: Project) -> tuple[ExecutionJob, ExecutionAttempt]:
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
        status=ExecutionJobStatus.VALIDATING,
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


def _spec(job: ExecutionJob, *, command: tuple[str, ...], timeout_s: float) -> ApprovedLaunchSpec:
    """Same fixed posture as `test_execution_real_docker_lifecycle.py`'s
    own `_spec()` helper, duplicated locally per this repo's established
    per-file self-containment convention -- alpine, not the production
    image, for the long-running timeout workload."""
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


async def _get_asset(asset_id: uuid.UUID) -> Asset:
    async with async_session_factory() as verify_session:
        asset = await AssetRepository(verify_session).get_by_id(asset_id)
        assert asset is not None
        return asset


def _assert_container_gone(docker_client, name: str) -> None:
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(name)


async def _run(job, attempt, project, spec) -> tuple:
    async with async_session_factory() as session:
        outcome = await execute_approved_launch(
            spec,
            attempt_id=attempt.id,
            container_name=attempt.container_name,
            project_id=project.id,
            owner_id=project.owner_id,
            job_id=job.id,
            session=session,
        )
        return outcome


# ---------------------------------------------------------------------------
# T1 -- normal success still works.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t1_normal_success_still_works(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo ok; exit 0"), timeout_s=5.0)

    outcome = await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )

    assert outcome.succeeded is True
    assert outcome.exit_code == 0
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.SUCCEEDED
    assert reloaded_job.completed_at is not None
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.EXITED
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T2 -- normal non-zero failure still works.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t2_normal_failure_still_works(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "exit 7"), timeout_s=5.0)

    outcome = await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )

    assert outcome.succeeded is False
    assert outcome.exit_code == 7
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.FAILED
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.EXITED
    assert reloaded_attempt.exit_code == 7
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T3 -- container runs longer than timeout.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t3_timeout_terminates_and_persists_timed_out(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=2.0)

    outcome = await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )

    assert outcome.succeeded is False
    assert outcome.exit_code == 137  # SIGKILL, OBSERVED
    reloaded_job = await _reload_job(job.id)
    assert reloaded_job.status == ExecutionJobStatus.TIMED_OUT
    assert reloaded_job.completed_at is not None
    reloaded_attempt = await _reload_attempt(attempt.id)
    assert reloaded_attempt.status == ExecutionAttemptStatus.EXITED
    assert reloaded_attempt.container_id is not None
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T4 -- timeout output is still collectible.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t4_timeout_output_still_collectible(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "echo before-timeout; sleep 30"), timeout_s=2.0)

    outcome = await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )

    asset = await _get_asset(outcome.result_asset_id)
    assert "before-timeout" in asset.asset_metadata["stdout"]
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T5 -- timeout cleanup is idempotent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_timeout_cleanup_idempotent(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=2.0)

    await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )

    # First .get() already confirms NotFound; a second attempt, and a
    # direct second remove(), must both behave safely (OBSERVED
    # generically for remove() in the 7B.25 spike; reconfirmed here
    # specifically after a KILL-based termination, not a natural exit).
    _assert_container_gone(docker_client, attempt.container_name)
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(attempt.container_name)


# ---------------------------------------------------------------------------
# T6 -- cancellation requested while RUNNING.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t6_cancellation_while_running(project, docker_client):
    async with async_session_factory() as setup_session:
        job, attempt = await _make_job_and_attempt(setup_session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=30.0)  # long timeout -- cancellation must win first

    launch_task = asyncio.create_task(_run(job, attempt, project, spec))
    await asyncio.sleep(1.5)  # >= one _POLL_INTERVAL_S cycle -- job.status should be RUNNING by now

    running_job = await _reload_job(job.id)
    assert running_job.status == ExecutionJobStatus.RUNNING

    async with async_session_factory() as cancel_session:
        cancelled = await request_execution_cancellation(project.owner_id, job.id, cancel_session)
    assert cancelled is not None
    assert cancelled.status == ExecutionJobStatus.CANCEL_REQUESTED

    outcome = await launch_task

    assert outcome.succeeded is False
    assert outcome.exit_code == 137
    final_job = await _reload_job(job.id)
    assert final_job.status == ExecutionJobStatus.CANCELLED
    final_attempt = await _reload_attempt(attempt.id)
    assert final_attempt.status == ExecutionAttemptStatus.EXITED
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T7 -- cancellation requested near natural completion: no double-
# transition or corrupted terminal state.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t7_cancellation_near_natural_completion_no_corruption(project, docker_client):
    async with async_session_factory() as setup_session:
        job, attempt = await _make_job_and_attempt(setup_session, project)
    # Short enough to plausibly finish naturally around when the
    # cancellation request lands, long enough for RUNNING to be
    # observable first.
    spec = _spec(job, command=("sh", "-c", "sleep 1.2; exit 0"), timeout_s=10.0)

    launch_task = asyncio.create_task(_run(job, attempt, project, spec))
    await asyncio.sleep(1.0)
    async with async_session_factory() as cancel_session:
        await request_execution_cancellation(project.owner_id, job.id, cancel_session)

    outcome = await launch_task

    final_job = await _reload_job(job.id)
    final_attempt = await _reload_attempt(attempt.id)
    # Exactly one clean terminal combination -- never a mix (e.g.
    # attempt EXITED but job still RUNNING/CANCEL_REQUESTED).
    assert final_attempt.status == ExecutionAttemptStatus.EXITED
    assert final_job.status in (ExecutionJobStatus.CANCELLED, ExecutionJobStatus.SUCCEEDED)
    if final_job.status == ExecutionJobStatus.SUCCEEDED:
        assert outcome.succeeded is True and outcome.exit_code == 0
    else:
        assert outcome.succeeded is False
    assert final_job.completed_at is not None
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T8 -- cancellation requested after completion: no destructive action.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t8_cancellation_after_completion_is_a_no_op(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sh", "-c", "exit 0"), timeout_s=5.0)

    await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )
    before = await _reload_job(job.id)
    assert before.status == ExecutionJobStatus.SUCCEEDED

    async with async_session_factory() as cancel_session:
        result = await request_execution_cancellation(project.owner_id, job.id, cancel_session)

    assert result is None  # not RUNNING -- no transition
    after = await _reload_job(job.id)
    assert after.status == ExecutionJobStatus.SUCCEEDED  # already-terminal result remains authoritative


# ---------------------------------------------------------------------------
# T9 -- two simultaneous cancellation requests.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t9_two_simultaneous_cancellation_requests(project, docker_client):
    async with async_session_factory() as setup_session:
        job, attempt = await _make_job_and_attempt(setup_session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=30.0)

    launch_task = asyncio.create_task(_run(job, attempt, project, spec))
    await asyncio.sleep(1.5)

    async def _attempt_cancel():
        async with async_session_factory() as cancel_session:
            return await request_execution_cancellation(project.owner_id, job.id, cancel_session)

    result_a, result_b = await asyncio.gather(_attempt_cancel(), _attempt_cancel())
    results = [result_a, result_b]
    successes = [r for r in results if r is not None]
    rejections = [r for r in results if r is None]
    assert len(successes) == 1, "exactly one durable cancellation transition"
    assert len(rejections) == 1
    # Both callers returned safely -- no exception from either.

    outcome = await launch_task
    assert outcome.succeeded is False
    final_job = await _reload_job(job.id)
    assert final_job.status == ExecutionJobStatus.CANCELLED
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T10 -- timeout and cancellation race: exactly one terminal outcome.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t10_timeout_and_cancellation_race_exactly_one_outcome(project, docker_client):
    async with async_session_factory() as setup_session:
        job, attempt = await _make_job_and_attempt(setup_session, project)
    # A short timeout AND a near-simultaneous cancellation request --
    # _wait_bounded's fixed check order (deadline checked before the
    # fresh cancellation read, every iteration) makes this
    # deterministic: whichever the loop is already past when it next
    # wakes decides the single outcome.
    spec = _spec(job, command=("sleep", "30"), timeout_s=1.5)

    launch_task = asyncio.create_task(_run(job, attempt, project, spec))
    await asyncio.sleep(1.2)
    async with async_session_factory() as cancel_session:
        await request_execution_cancellation(project.owner_id, job.id, cancel_session)

    outcome = await launch_task

    final_job = await _reload_job(job.id)
    final_attempt = await _reload_attempt(attempt.id)
    assert final_job.status in (ExecutionJobStatus.TIMED_OUT, ExecutionJobStatus.CANCELLED)
    assert final_attempt.status == ExecutionAttemptStatus.EXITED
    assert outcome.succeeded is False
    # No invalid combination: exactly one status, a real exit code, no exception.
    assert outcome.exit_code == 137
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T11 -- repeated terminate/remove cleanup handled safely.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t11_repeated_terminate_remove_handled_safely(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=2.0)

    await execute_approved_launch(
        spec, attempt_id=attempt.id, container_name=attempt.container_name,
        project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
    )

    # Container already removed by the launcher's own cleanup -- a
    # second manual kill()/remove() attempt must raise NotFound, not
    # some other unhandled error.
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(attempt.container_name).kill()


# ---------------------------------------------------------------------------
# T12 -- daemon/API failure during termination (narrow MOCKED test --
# forcing a genuine mid-termination daemon failure deterministically
# against a real daemon is impractical, exactly the exception this
# phase's own instructions anticipate).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t12_daemon_failure_during_termination_bounded_diagnostic(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=2.0)

    with patch(
        "execution_launcher.launcher._terminate_container",
        side_effect=docker.errors.DockerException("simulated daemon failure during kill"),
    ):
        outcome = await execute_approved_launch(
            spec, attempt_id=attempt.id, container_name=attempt.container_name,
            project_id=project.id, owner_id=project.owner_id, job_id=job.id, session=session,
        )

    assert outcome.succeeded is False
    assert outcome.exit_code is None
    assert outcome.diagnostic is not None and "simulated daemon failure" in outcome.diagnostic
    final_attempt = await _reload_attempt(attempt.id)
    assert final_attempt.status == ExecutionAttemptStatus.UNKNOWN  # no false EXITED claim
    final_job = await _reload_job(job.id)
    assert final_job.status == ExecutionJobStatus.FAILED
    assert final_job.reason is not None

    # Best-effort cleanup was still attempted despite the failure --
    # the real container (never actually killed by the mock) is
    # removed by the launcher's own _best_effort_remove(force=True).
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T13 -- security posture unchanged under the bounded-wait path.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t13_security_posture_unchanged_under_bounded_wait(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, command=("sleep", "30"), timeout_s=2.0)

    # Inspect WHILE running, before the timeout fires -- proves posture
    # on a container that actually goes through the new polling path.
    launch_task = asyncio.create_task(_run(job, attempt, project, spec))
    await asyncio.sleep(0.5)
    container = docker_client.containers.get(attempt.container_name)
    attrs = container.attrs
    assert attrs["HostConfig"]["NetworkMode"] == "none"
    assert attrs["HostConfig"]["Privileged"] is False
    assert attrs["HostConfig"]["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in "".join(attrs["HostConfig"]["SecurityOpt"])
    assert attrs["HostConfig"]["ReadonlyRootfs"] is True
    assert attrs["Config"]["User"] == "10001:10001"
    assert attrs["HostConfig"]["Memory"] == 256 * 1024 * 1024
    assert attrs["HostConfig"]["PidsLimit"] == 32

    await launch_task  # let the timeout run its course, cleanup included
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T14 -- real front-door equation execution still produces 14.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
def test_t14_real_front_door_still_produces_14():
    async def _setup():
        async with async_session_factory() as setup_session:
            project = await _make_owner_and_project(setup_session, name="T14 Front Door Project")
            service = ExperimentPlanService(setup_session)
            plan = await service.create_from_equation(
                project.owner_id,
                ExperimentPlanCreateFromEquation(
                    project_id=project.id, title="7B.28 T14 real front door", expression="2 + 3 * 4"
                ),
            )
            job = await service.request_execution(project.owner_id, plan.id, "baseline")
            return project, job

    project_, job_ = asyncio.run(_setup())
    try:
        result = launch_execution_job(str(job_.id))
        assert result["status"] == "execution_succeeded"

        async def _verify():
            async with async_session_factory() as verify_session:
                jr = ExecutionJobRepository(verify_session)
                job = await jr.get_by_id(job_.id)
                assert job.status == ExecutionJobStatus.SUCCEEDED

                attempts_result = await verify_session.execute(
                    select(ExecutionAttempt).where(ExecutionAttempt.execution_job_id == job_.id)
                )
                attempt = attempts_result.scalars().one()
                assets_result = await verify_session.execute(
                    select(Asset).where(
                        Asset.asset_metadata["execution_attempt_id"].astext == str(attempt.id)
                    )
                )
                result_asset = assets_result.scalars().one()
                import json

                stdout = json.loads(result_asset.asset_metadata["stdout"])
                assert stdout == {"result": 14.0, "error": None}

        asyncio.run(_verify())
    finally:
        async def _teardown():
            async with async_session_factory() as teardown_session:
                user = await teardown_session.get(User, project_.owner_id)
                if user is not None:
                    await teardown_session.delete(user)
                    await teardown_session.commit()

        asyncio.run(_teardown())


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_no_new_docker_sdk_importer_introduced():
    import ast
    import pathlib

    def _imports_any_of(source: str, module_names: set[str]) -> bool:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] in module_names for alias in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in module_names:
                    return True
        return False

    import app.modules.execution.repository as repository_module
    import app.modules.execution.service as service_module
    import execution_launcher.docker_policy as docker_policy_module
    import execution_launcher.translator as translator_module

    for module in (docker_policy_module, translator_module, repository_module, service_module):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert not _imports_any_of(source, {"docker"})
