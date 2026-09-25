"""Sprint 16 Phase 7B.25 -- REAL Docker execution & lifecycle tests.

Every test in this file (except explicitly marked structural/unit ones)
runs against a REAL Docker daemon and REAL Postgres -- no fake Docker
client, no mocked docker-py calls, matching the phase's explicit
"Mocks/stubs may only be used for narrow error-isolation/unit tests
where real external execution would be inappropriate" boundary. Only
`test_translation_is_pure_no_docker_call` and the isolation-boundary
checks at the bottom are unit/structural (labeled accordingly).

`ApprovedLaunchSpec` is constructed directly here, bypassing
`docker_policy.validate_and_approve()` -- the SAME established pattern
`test_execution_docker_policy.py` already uses (see its own
`test_no_approved_launch_spec_construction_outside_docker_policy`,
which scans only the non-test `execution_launcher/` package, not
`tests/`). This is necessary because `_APPROVED_IMAGE_DIGESTS` in
`docker_policy.py` contains only `TEST-ONLY-NOT-A-REAL-IMAGE@sha256:...`
placeholder digests -- no real, pullable dispatcher image has been
built for this project yet (see the phase report's Executive Verdict
for what this means for the full production path). Every test here
therefore builds its own `ApprovedLaunchSpec` with a real, already
locally-available image (`alpine:3.19`) instead, exercising the REAL
launcher code path end-to-end.

Container naming still uses the real, unmodified
`container_name_for_attempt()` (`aikdap-exec-{attempt_id}`) -- nothing
about the deterministic-naming contract is test-specific.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

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
from app.modules.execution.repository import ExecutionAttemptRepository
from app.modules.execution.service import container_name_for_attempt
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from execution_launcher.models import (
    ApprovedLaunchSpec,
    ExecutionCapability,
    ExecutionOperation,
    MountSpec,
    ResourceClass,
)
from execution_launcher.launcher import (
    MAX_OUTPUT_BYTES,
    ExecutionOutcome,
    _best_effort_remove,
    _docker_create_kwargs,
    execute_approved_launch,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    await engine.dispose()
    yield


@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()


async def _make_owner_and_project(session, *, name: str) -> Project:
    user = User(
        email=f"pytest-realdocker-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Real Docker Lifecycle Test User",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    project = Project(
        owner_id=user.id,
        name=name,
        description="Created by test_execution_real_docker_lifecycle.py",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def project(session) -> AsyncIterator[Project]:
    proj = await _make_owner_and_project(session, name="Real Docker Lifecycle Test Project")
    yield proj
    user = await session.get(User, proj.owner_id)
    if user is not None:
        await session.delete(user)
        await session.commit()


async def _make_job_and_attempt(session, project: Project) -> tuple[ExecutionJob, ExecutionAttempt]:
    """A real `ExecutionJob` (VALIDATING, as it would be mid-launch) with
    one real `ExecutionAttempt(PENDING_CREATE)` -- the exact durable
    starting point this phase's launcher is required to pick up from."""
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


def _spec(
    job: ExecutionJob,
    *,
    image: str,
    command: tuple[str, ...],
    mounts: tuple[MountSpec, ...] = (),
    timeout_s: float = 10.0,
) -> ApprovedLaunchSpec:
    """The fixed security posture `execution_launcher/docker_policy.py`
    already enforces, reproduced literally here (not imported, since
    those constants are private to that module) -- the same values a
    real `validate_and_approve()` call would have produced for
    CLASS_EXPR, just paired with a REAL image instead of the fake
    approved digest. `timeout_s` defaults to CLASS_EXPR's real
    10-second policy value (Sprint 16 Phase 7B.28) but is overridable
    per-test."""
    return ApprovedLaunchSpec(
        job_id=job.id,
        image=image,
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
        mounts=mounts,
        environment={},
        command=command,
        timeout_s=timeout_s,
    )


async def _reload_attempt(attempt_id: uuid.UUID) -> ExecutionAttempt:
    async with async_session_factory() as verify_session:
        attempt = await verify_session.get(ExecutionAttempt, attempt_id)
        assert attempt is not None
        return attempt


async def _attempts_for_job(job_id: uuid.UUID) -> list[ExecutionAttempt]:
    async with async_session_factory() as verify_session:
        result = await verify_session.execute(
            select(ExecutionAttempt).where(ExecutionAttempt.execution_job_id == job_id)
        )
        return list(result.scalars().all())


async def _get_asset(asset_id: uuid.UUID) -> Asset:
    async with async_session_factory() as verify_session:
        asset = await AssetRepository(verify_session).get_by_id(asset_id)
        assert asset is not None
        return asset


def _assert_container_gone(docker_client, name: str) -> None:
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(name)


# ---------------------------------------------------------------------------
# T1 -- successful command, exit code 0.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t1_successful_execution_full_lifecycle(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, image="alpine:3.19", command=("sh", "-c", "echo hello-t1; exit 0"))

    outcome = await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    assert isinstance(outcome, ExecutionOutcome)
    assert outcome.succeeded is True
    assert outcome.exit_code == 0
    assert outcome.result_asset_id is not None

    reloaded = await _reload_attempt(attempt.id)
    assert reloaded.container_id is not None and len(reloaded.container_id) == 64
    assert reloaded.status == ExecutionAttemptStatus.EXITED
    assert reloaded.exit_code == 0
    assert reloaded.started_at is not None
    assert reloaded.completed_at is not None
    assert reloaded.completed_at >= reloaded.started_at

    asset = await _get_asset(outcome.result_asset_id)
    assert asset.asset_metadata["exit_code"] == 0
    assert "hello-t1" in asset.asset_metadata["stdout"]
    assert asset.asset_metadata["stdout_truncated"] is False

    attempts = await _attempts_for_job(job.id)
    assert len(attempts) == 1, "no duplicate attempt created"

    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T2 -- non-zero exit code (17).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t2_nonzero_exit_classified_as_failed(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, image="alpine:3.19", command=("sh", "-c", "echo about-to-fail 1>&2; exit 17"))

    outcome = await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    # No Docker API exception -- the whole call returned normally.
    assert outcome.succeeded is False
    assert outcome.exit_code == 17

    reloaded = await _reload_attempt(attempt.id)
    assert reloaded.status == ExecutionAttemptStatus.EXITED  # exited, not "failed" -- no such enum value
    assert reloaded.exit_code == 17
    assert reloaded.error_message is not None and "17" in reloaded.error_message

    asset = await _get_asset(outcome.result_asset_id)
    assert asset.asset_metadata["exit_code"] == 17
    assert "about-to-fail" in asset.asset_metadata["stderr"]

    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T3 -- ImageNotFound.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_image_not_found_no_phantom_state(session, project):
    job, attempt = await _make_job_and_attempt(session, project)
    # The REAL shape of docker_policy.py's own (fake) approved digest --
    # proves directly that today's real approved catalog cannot launch.
    fake_approved_digest = "TEST-ONLY-NOT-A-REAL-IMAGE@sha256:" + "e" * 64
    spec = _spec(job, image=fake_approved_digest, command=("true",))

    outcome = await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    assert outcome.succeeded is False
    assert outcome.exit_code is None
    assert outcome.result_asset_id is None

    reloaded = await _reload_attempt(attempt.id)
    assert reloaded.container_id is None, "no phantom container_id from a failed create()"
    assert reloaded.status == ExecutionAttemptStatus.PENDING_CREATE, "no invented failure enum"
    assert reloaded.error_message is not None and "image" in reloaded.error_message.lower()


# ---------------------------------------------------------------------------
# T4 -- name collision (409).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t4_name_collision_existing_container_unaffected(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)

    pre_existing = docker_client.containers.create(
        "alpine:3.19", command=["sleep", "5"], name=attempt.container_name
    )
    try:
        spec = _spec(job, image="alpine:3.19", command=("true",))
        outcome = await execute_approved_launch(
            spec,
            attempt_id=attempt.id,
            container_name=attempt.container_name,
            project_id=project.id,
            owner_id=project.owner_id,
            job_id=job.id,
            session=session,
        )

        assert outcome.succeeded is False
        assert outcome.exit_code is None

        reloaded = await _reload_attempt(attempt.id)
        assert reloaded.container_id is None
        assert reloaded.status == ExecutionAttemptStatus.PENDING_CREATE
        assert reloaded.error_message is not None
        assert "409" in reloaded.error_message or "conflict" in reloaded.error_message.lower()

        pre_existing.reload()
        assert pre_existing.attrs["State"]["Status"] in ("created", "running")
    finally:
        pre_existing.remove(force=True)


# ---------------------------------------------------------------------------
# T5 -- create succeeds, start fails (a command that does not exist in
# the image -- OBSERVED empirically before writing this test: docker-py
# raises APIError(status_code=400) from start(), and inspect() shows
# State.Status remains "created", never "running").
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t5_start_failure_container_id_durable_status_accurate(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    spec = _spec(job, image="alpine:3.19", command=("/definitely-does-not-exist-binary",))

    outcome = await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    assert outcome.succeeded is False
    assert outcome.exit_code is None

    reloaded = await _reload_attempt(attempt.id)
    assert reloaded.container_id is not None, "create() succeeded -- id must be durable"
    assert reloaded.status == ExecutionAttemptStatus.CREATED, (
        "start failed but Docker itself never left 'created' -- CREATED is still accurate, "
        "not UNKNOWN"
    )
    assert reloaded.error_message is not None and "start failed" in reloaded.error_message.lower()

    # Cleanup attempted by the launcher itself (best-effort, on the
    # exception path) -- confirm it actually worked.
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T6 -- process disappears between create() and start() (a genuinely
# separate OS process, matching the real Docker spike's Test 10 method).
# No recovery implemented or asserted -- purely observational.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
def test_t6_crash_between_create_and_start_observable_by_separate_process(monkeypatch):
    import os
    import subprocess
    import sys

    async def _setup():
        async with async_session_factory() as db_session:
            proj = await _make_owner_and_project(db_session, name="T6 real docker project")
            job, attempt = await _make_job_and_attempt(db_session, proj)
            return proj, job, attempt

    project_, job_, attempt_ = asyncio.run(_setup())

    script = (
        "import asyncio, uuid\n"
        "from app.database.session import async_session_factory\n"
        "from execution_launcher.launcher import _docker_create_kwargs\n"
        "from execution_launcher.models import ApprovedLaunchSpec\n"
        "import docker\n"
        "async def main():\n"
        f"    attempt_id = uuid.UUID('{attempt_.id}')\n"
        "    async with async_session_factory() as session:\n"
        "        from app.modules.execution.repository import ExecutionAttemptRepository\n"
        "        attempt = await ExecutionAttemptRepository(session).get_by_id(attempt_id)\n"
        "        spec = ApprovedLaunchSpec(\n"
        f"            job_id=uuid.UUID('{job_.id}'), image='alpine:3.19', network_mode='none',\n"
        "            privileged=False, cap_drop=('ALL',), security_opt=('no-new-privileges',),\n"
        "            read_only=True, user='10001:10001', mem_limit_mb=256, memswap_limit_mb=256,\n"
        "            cpus=1.0, pids_limit=32, tmpfs_mb=64, mounts=(), environment={},\n"
        "            command=('sleep', '2'), timeout_s=10.0,\n"
        "        )\n"
        "        kwargs = _docker_create_kwargs(spec, container_name=attempt.container_name)\n"
        "        client = docker.from_env()\n"
        "        container = client.containers.create(**kwargs)\n"
        "        attempt.container_id = container.id\n"
        "        await session.commit()\n"
        "        print(container.id)\n"
        "    # Deliberately exits here -- no start(), simulating a crash.\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(os.path.dirname(__file__)) or ".",
    )
    assert result.returncode == 0, result.stderr[-3000:]
    persisted_container_id = result.stdout.strip().splitlines()[-1]

    client = docker.from_env()
    try:
        observed = client.containers.get(attempt_.container_name)
        observed.reload()
        assert observed.id == persisted_container_id
        assert observed.attrs["State"]["Status"] == "created"

        reloaded = asyncio.run(_reload_attempt(attempt_.id))
        assert reloaded.container_id == observed.id, "durable DB state matches real Docker identity"
        assert reloaded.status == ExecutionAttemptStatus.PENDING_CREATE, (
            "the crashed process never reached the CREATED-status commit -- "
            "an accepted, undiagnosed gap this phase does not fix (no recovery implemented)"
        )
    finally:
        try:
            client.containers.get(attempt_.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass
        asyncio.run(_teardown_project(project_))


async def _teardown_project(project: Project) -> None:
    async with async_session_factory() as session:
        user = await session.get(User, project.owner_id)
        if user is not None:
            await session.delete(user)
            await session.commit()


# ---------------------------------------------------------------------------
# T7 -- process disappears between start() and wait(): the container
# continues independently and its final state remains inspectable.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
def test_t7_crash_between_start_and_wait_container_continues_independently():
    import os
    import subprocess
    import sys

    async def _setup():
        async with async_session_factory() as db_session:
            proj = await _make_owner_and_project(db_session, name="T7 real docker project")
            job, attempt = await _make_job_and_attempt(db_session, proj)
            return proj, job, attempt

    project_, job_, attempt_ = asyncio.run(_setup())

    script = (
        "import asyncio, uuid\n"
        "from app.database.session import async_session_factory\n"
        "from execution_launcher.launcher import _docker_create_kwargs\n"
        "from execution_launcher.models import ApprovedLaunchSpec\n"
        "from app.modules.execution.repository import ExecutionAttemptRepository\n"
        "import docker\n"
        "async def main():\n"
        f"    attempt_id = uuid.UUID('{attempt_.id}')\n"
        "    async with async_session_factory() as session:\n"
        "        attempt = await ExecutionAttemptRepository(session).get_by_id(attempt_id)\n"
        "        spec = ApprovedLaunchSpec(\n"
        f"            job_id=uuid.UUID('{job_.id}'), image='alpine:3.19', network_mode='none',\n"
        "            privileged=False, cap_drop=('ALL',), security_opt=('no-new-privileges',),\n"
        "            read_only=True, user='10001:10001', mem_limit_mb=256, memswap_limit_mb=256,\n"
        "            cpus=1.0, pids_limit=32, tmpfs_mb=64, mounts=(), environment={},\n"
        "            command=('sh', '-c', 'sleep 0.3; exit 9'), timeout_s=10.0,\n"
        "        )\n"
        "        kwargs = _docker_create_kwargs(spec, container_name=attempt.container_name)\n"
        "        client = docker.from_env()\n"
        "        container = client.containers.create(**kwargs)\n"
        "        attempt.container_id = container.id\n"
        "        await session.commit()\n"
        "        container.start()\n"
        "        print(container.id)\n"
        "    # Deliberately exits here -- no wait(), simulating a crash.\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(os.path.dirname(__file__)) or ".",
    )
    assert result.returncode == 0, result.stderr[-3000:]

    time.sleep(0.6)  # give the daemon-managed container time to finish on its own

    client = docker.from_env()
    try:
        observed = client.containers.get(attempt_.container_name)
        observed.reload()
        assert observed.attrs["State"]["Status"] == "exited"
        assert observed.attrs["State"]["ExitCode"] == 9
        wait_after_crash = observed.wait()  # a THIRD process calling wait() on an already-exited container
        assert wait_after_crash["StatusCode"] == 9
    finally:
        try:
            client.containers.get(attempt_.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass
        asyncio.run(_teardown_project(project_))


# ---------------------------------------------------------------------------
# T8 -- repeated cleanup is idempotent.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t8_repeated_cleanup_idempotent(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    container = docker_client.containers.create(
        "alpine:3.19", command=["true"], name=attempt.container_name
    )
    container.start()
    container.wait()

    await _best_effort_remove(docker_client, container, attempt_id=attempt.id)  # real remove
    _assert_container_gone(docker_client, attempt.container_name)

    await _best_effort_remove(docker_client, container, attempt_id=attempt.id)  # must not raise


# ---------------------------------------------------------------------------
# T9 -- output bound: >1 MiB of stdout is truncated, never unbounded.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
@pytest.mark.asyncio
async def test_t9_output_exceeding_bound_is_truncated(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    over_bound_bytes = MAX_OUTPUT_BYTES + 200_000
    spec = _spec(
        job,
        image="alpine:3.19",
        command=("sh", "-c", f"head -c {over_bound_bytes} /dev/zero | tr '\\0' 'a'"),
    )

    outcome = await execute_approved_launch(
        spec,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    assert outcome.succeeded is True
    asset = await _get_asset(outcome.result_asset_id)
    assert asset.asset_metadata["stdout_truncated"] is True
    assert len(asset.asset_metadata["stdout"]) == MAX_OUTPUT_BYTES

    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T10 -- security/resource posture confirmed via REAL Docker inspect,
# not source review.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("alpine:3.19")
def test_t10_security_and_resource_posture_confirmed_by_real_inspect(docker_client):
    fake_job_id = uuid.uuid4()
    spec = ApprovedLaunchSpec(
        job_id=fake_job_id,
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
        command=("true",),
        timeout_s=10.0,
    )
    name = f"aikdap-exec-t10-{uuid.uuid4().hex[:8]}"
    kwargs = _docker_create_kwargs(spec, container_name=name)
    container = docker_client.containers.create(**kwargs)
    try:
        container.reload()
        host_config = container.attrs["HostConfig"]
        config = container.attrs["Config"]

        assert host_config["NetworkMode"] == "none"
        assert host_config["Privileged"] is False
        assert host_config["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in (host_config["SecurityOpt"] or [])
        assert host_config["ReadonlyRootfs"] is True
        assert config["User"] == "10001:10001"
        assert host_config["NanoCpus"] == 1_000_000_000
        assert host_config["Memory"] == 256 * 1024 * 1024
        assert host_config["PidsLimit"] == 32
        assert "/tmp" in (container.attrs.get("HostConfig", {}).get("Tmpfs") or {}) or "/tmp" in (
            container.attrs.get("Config", {}).get("Volumes") or {}
        ) or True  # Tmpfs surfaces under HostConfig.Tmpfs on some engine versions -- checked below too
        # Direct source-of-truth check for tmpfs, independent of the above:
        assert host_config.get("Tmpfs", {}).get("/tmp") == "size=64m"
    finally:
        container.remove(force=True)


# ---------------------------------------------------------------------------
# Unit -- kwargs translation is pure (no Docker/network I/O at all).
# ---------------------------------------------------------------------------


def test_translation_is_pure_no_docker_call():
    """UNIT test: `_docker_create_kwargs` never touches the daemon --
    proven by constructing it with no Docker client in scope at all."""
    spec = ApprovedLaunchSpec(
        job_id=uuid.uuid4(), image="alpine:3.19", network_mode="none", privileged=False,
        cap_drop=("ALL",), security_opt=("no-new-privileges",), read_only=True,
        user="10001:10001", mem_limit_mb=256, memswap_limit_mb=256, cpus=1.0,
        pids_limit=32, tmpfs_mb=64,
        mounts=(MountSpec(source="/host/a.csv", destination="/input/a.csv", mode="ro"),),
        environment={"X": "1"}, command=("true",),
        timeout_s=10.0,
    )
    kwargs = _docker_create_kwargs(spec, container_name="aikdap-exec-unit-test")
    assert kwargs["image"] == "alpine:3.19"
    assert kwargs["name"] == "aikdap-exec-unit-test"
    assert kwargs["network_mode"] == "none"
    assert kwargs["nano_cpus"] == 1_000_000_000
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["memswap_limit"] == "256m"
    assert kwargs["tmpfs"] == {"/tmp": "size=64m"}
    assert kwargs["volumes"] == {"/host/a.csv": {"bind": "/input/a.csv", "mode": "ro"}}
    assert kwargs["environment"] == {"X": "1"}


# ---------------------------------------------------------------------------
# Isolation boundary -- no OTHER application component imports Docker.
# ---------------------------------------------------------------------------


def test_no_router_service_reconciliation_or_task_module_imports_docker():
    import ast
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    targets = [
        backend / "app" / "modules" / "execution" / "router.py",
        backend / "app" / "modules" / "execution" / "service.py",
        backend / "app" / "modules" / "execution" / "repository.py",
        backend / "app" / "modules" / "research" / "router.py",
        backend / "app" / "modules" / "research" / "experiment_service.py",
        backend / "app" / "workers" / "reconciliation.py",
        backend / "app" / "workers" / "worker.py",
        backend / "app" / "workers" / "tasks.py",
        backend / "app" / "agents",
    ]

    def imports_docker(path: pathlib.Path) -> bool:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                a.name == "docker" or a.name.startswith("docker.") for a in node.names
            ):
                return True
            if isinstance(node, ast.ImportFrom) and (
                node.module == "docker" or (node.module or "").startswith("docker.")
            ):
                return True
        return False

    offenders = []
    for target in targets:
        files = [target] if target.is_file() else list(target.rglob("*.py"))
        for f in files:
            if imports_docker(f):
                offenders.append(str(f.relative_to(backend)))
    assert offenders == [], offenders
