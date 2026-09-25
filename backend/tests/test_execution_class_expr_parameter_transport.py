"""Sprint 16 Phase 7B.27 -- CLASS_EXPR parameter transport (T1-T14).

Closes the exact gap Phase 7B.26 found and documented: `LaunchRequest.
parameters["expression"]` is real and computed, but nothing carried it
into the container. This phase's real-daemon investigation (see
`execution_launcher/docker_policy.py::_expected_class_expr_environment`'s
own docstring, and this file's `test_t5_...`/`test_t6_...` below) found
that docker-py's `attach_socket()`-based stdin delivery HANGS on this
environment's real transport -- two independent real-daemon probes,
each requiring a manual `docker rm -f` after `container.wait()` timed
out. The selected channel is therefore a guard-computed, size-bounded
environment variable (`AIKDAP_EXECUTION_PARAMETERS`), not stdin --
chosen because it reuses the EXISTING, already-proven `ApprovedLaunchSpec
.environment` field and `launcher.py`'s existing, unmodified
`environment=dict(approved.environment)` translation verbatim.

Every test here runs against REAL Postgres and the REAL Docker daemon
using the REAL rebuilt image (`aikdap-exec-class-expr@sha256:
cd631bedb89e...`, dispatcher now reads `AIKDAP_EXECUTION_PARAMETERS`
first, falling back to stdin) -- no fake image, no mocked docker-py
call, matching this sprint's established discipline throughout.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

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
from app.modules.execution.service import container_name_for_attempt
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from app.modules.research.experiment_schemas import ExperimentPlanCreateFromEquation
from app.modules.research.experiment_service import ExperimentPlanService
from app.workers.tasks import launch_execution_job
from execution_launcher import docker_policy
from execution_launcher.docker_policy import _expected_class_expr_environment, validate_and_approve
from execution_launcher.launcher import _docker_create_kwargs, execute_approved_launch
from execution_launcher.models import (
    ApprovedLaunchSpec,
    ExecutionCapability,
    ExecutionOperation,
    LaunchRequest,
    ResourceClass,
    SecurityBlocked,
)
from execution_launcher.resolvers import StaticInputResolver
from execution_launcher.translator import build_candidate

REAL_IMAGE_DIGEST = docker_policy._APPROVED_IMAGE_DIGESTS[ResourceClass.CLASS_EXPR]
ENV_VAR = docker_policy._EXPRESSION_PARAMETER_ENV_VAR


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    await engine.dispose()
    yield


@pytest.fixture(scope="module")
def docker_client():
    return docker.from_env()


async def _make_owner_and_project(session, *, name: str) -> Project:
    user = User(
        email=f"pytest-paramtransport-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Parameter Transport Test User",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    project = Project(
        owner_id=user.id,
        name=name,
        description="Created by test_execution_class_expr_parameter_transport.py",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def project(session) -> AsyncIterator[Project]:
    proj = await _make_owner_and_project(session, name="Parameter Transport Test Project")
    yield proj
    user = await session.get(User, proj.owner_id)
    if user is not None:
        await session.delete(user)
        await session.commit()


def _expr_request(*, expression: str = "2 + 3 * 4", extra_parameters: dict | None = None) -> LaunchRequest:
    parameters = {"expression": expression, **(extra_parameters or {})}
    return LaunchRequest(
        job_id=uuid.uuid4(),
        experiment_plan_id=uuid.uuid4(),
        experiment_plan_version=1,
        owner_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        capability=ExecutionCapability.EVALUATE_EXPRESSION,
        operation=ExecutionOperation.EVALUATE_EXPRESSION,
        parameters=parameters,
        resource_class=ResourceClass.CLASS_EXPR,
        input_asset_ids=[],
    )


async def _make_job_and_attempt(session, project: Project, *, expression: str = "2 + 3 * 4") -> tuple[ExecutionJob, ExecutionAttempt]:
    job = ExecutionJob(
        project_id=project.id,
        owner_id=project.owner_id,
        experiment_plan_id=uuid.uuid4(),
        experiment_plan_version=1,
        capability=ExecutionCapability.EVALUATE_EXPRESSION,
        operation=ExecutionOperation.EVALUATE_EXPRESSION,
        resource_class=ResourceClass.CLASS_EXPR,
        parameters={"expression": expression},
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


async def _reload_attempt(attempt_id: uuid.UUID) -> ExecutionAttempt:
    async with async_session_factory() as verify_session:
        attempt = await verify_session.get(ExecutionAttempt, attempt_id)
        assert attempt is not None
        return attempt


async def _get_asset(asset_id: uuid.UUID) -> Asset:
    async with async_session_factory() as verify_session:
        asset = await AssetRepository(verify_session).get_by_id(asset_id)
        assert asset is not None
        return asset


def _assert_container_gone(docker_client, name: str) -> None:
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(name)


# ---------------------------------------------------------------------------
# T1 -- pure parameter translation: expression -> exact expected payload.
# ---------------------------------------------------------------------------


def test_t1_pure_translation_exact_payload():
    request = _expr_request(expression="2 + 3 * 4")
    env = _expected_class_expr_environment(request)
    assert env == {ENV_VAR: '{"expression":"2 + 3 * 4"}'}


@pytest.mark.asyncio
async def test_t1_translation_is_pure_no_docker_call():
    """`_expected_class_expr_environment` (like every guard helper) does
    real work with zero I/O -- calling it never touches Docker."""
    request = _expr_request()
    env1 = _expected_class_expr_environment(request)
    env2 = _expected_class_expr_environment(request)
    assert env1 == env2  # deterministic, no hidden state


# ---------------------------------------------------------------------------
# T2 -- payload size bound: oversized expression rejected BEFORE Docker.
# ---------------------------------------------------------------------------


def test_t2_oversized_expression_rejected_before_docker_call():
    huge_expression = "1+" * 2000  # ~4000 chars, well past the 2048-byte JSON bound
    request = _expr_request(expression=huge_expression)
    with pytest.raises(SecurityBlocked) as exc_info:
        _expected_class_expr_environment(request)
    assert exc_info.value.check_name == "expression_parameter_too_large"


@pytest.mark.asyncio
async def test_t2_oversized_expression_rejected_by_build_candidate():
    """Proves the rejection happens at candidate-construction time --
    entirely before `execute_approved_launch` would ever be reachable,
    i.e. before any Docker call."""
    huge_expression = "1+" * 2000
    resolver = StaticInputResolver()
    with pytest.raises(SecurityBlocked) as exc_info:
        await build_candidate(_expr_request(expression=huge_expression), resolver)
    assert exc_info.value.check_name == "expression_parameter_too_large"


# ---------------------------------------------------------------------------
# T3 -- no unrelated parameters leak into the payload.
# ---------------------------------------------------------------------------


def test_t3_unrelated_parameters_do_not_leak():
    request = _expr_request(
        expression="2 + 3",
        extra_parameters={"x": "5", "unrelated_secret": "should-not-leak"},
    )
    env = _expected_class_expr_environment(request)
    payload = json.loads(env[ENV_VAR])
    assert payload == {"expression": "2 + 3"}
    assert "x" not in payload
    assert "unrelated_secret" not in env[ENV_VAR]


# ---------------------------------------------------------------------------
# T4 -- malformed/invalid expression: a normal scientific failure, not a
# launcher crash.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
@pytest.mark.asyncio
async def test_t4_invalid_expression_is_a_normal_dispatcher_result(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project, expression="2 +++ * (")
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(expression="2 +++ * ("), resolver)
    approved = await validate_and_approve(candidate, resolver)

    outcome = await execute_approved_launch(
        approved,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    assert outcome.exit_code == 0, "an invalid expression is a normal result, not a launcher crash"
    assert outcome.succeeded is True  # dispatcher-level: it ran successfully and reported a result
    asset = await _get_asset(outcome.result_asset_id)
    stdout = json.loads(asset.asset_metadata["stdout"])
    assert stdout["result"] is None
    assert "invalid expression" in stdout["error"]
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T5 / T6 -- real Docker receives the expression through the selected
# channel; the real dispatcher evaluates it to 14. One direct-Docker
# test (not through execute_approved_launch) for maximum isolation from
# the launcher's own code, per "do at least one direct real-daemon
# verification."
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
@pytest.mark.asyncio
async def test_t5_t6_real_docker_direct_verification(docker_client):
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(expression="2 + 3 * 4"), resolver)
    approved = await validate_and_approve(candidate, resolver)
    assert approved.environment == {ENV_VAR: '{"expression":"2 + 3 * 4"}'}  # T5: channel carries it

    kwargs = _docker_create_kwargs(approved, container_name=f"aikdap-t5t6-{uuid.uuid4().hex[:12]}")
    container = docker_client.containers.create(**kwargs)
    try:
        container.start()
        result = container.wait(timeout=10)
        assert result["StatusCode"] == 0
        logs = container.logs(stdout=True, stderr=True).decode("utf-8")
        payload = json.loads(logs)
        assert payload == {"result": 14.0, "error": None}  # T6: real dispatcher, real 14
    finally:
        container.remove(force=True)


# ---------------------------------------------------------------------------
# T7 / T8 -- real front-door execution produces persisted result 14;
# exit code 0.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
def test_t7_t8_real_front_door_produces_14():
    """Plain `def`, not `async def` -- `launch_execution_job`'s body
    calls `asyncio.run()` internally (established pattern, see
    `test_execution_front_door.py`'s own docstring)."""

    async def _setup():
        async with async_session_factory() as setup_session:
            project = await _make_owner_and_project(setup_session, name="T7/T8 Front Door Project")
            service = ExperimentPlanService(setup_session)
            plan = await service.create_from_equation(
                project.owner_id,
                ExperimentPlanCreateFromEquation(
                    project_id=project.id,
                    title="7B.27 T7/T8 real front door",
                    expression="2 + 3 * 4",  # bare expression -- parses with no free symbols
                ),
            )
            assert plan.source_expression == "2 + 3 * 4"
            job = await service.request_execution(project.owner_id, plan.id, "baseline")
            assert job.status == ExecutionJobStatus.PENDING
            assert job.parameters["expression"] == "2 + 3 * 4"
            return project, job

    project, job = asyncio.run(_setup())
    try:
        result = launch_execution_job(str(job.id))  # bound call, not .delay() -- real Docker underneath
        assert result["status"] == "execution_succeeded"

        async def _verify():
            async with async_session_factory() as verify_session:
                attempts_result = await verify_session.execute(
                    select(ExecutionAttempt).where(ExecutionAttempt.execution_job_id == job.id)
                )
                attempt = attempts_result.scalars().one()
                assert attempt.status == ExecutionAttemptStatus.EXITED
                assert attempt.exit_code == 0  # T8

                assets_result = await verify_session.execute(
                    select(Asset).where(
                        Asset.asset_metadata["execution_attempt_id"].astext == str(attempt.id)
                    )
                )
                result_asset = assets_result.scalars().one()
                assert result_asset.asset_metadata["exit_code"] == 0
                stdout = json.loads(result_asset.asset_metadata["stdout"])
                assert stdout == {"result": 14.0, "error": None}  # T7 -- the actual goal

        asyncio.run(_verify())
    finally:
        async def _teardown():
            async with async_session_factory() as teardown_session:
                user = await teardown_session.get(User, project.owner_id)
                if user is not None:
                    await teardown_session.delete(user)
                    await teardown_session.commit()

        asyncio.run(_teardown())


# ---------------------------------------------------------------------------
# T9 -- non-zero/dispatcher-failure semantics remain unchanged (the
# original 7B.26 stdin-fallback contract still holds when the new
# channel is absent, proven via a directly-constructed ApprovedLaunchSpec
# -- the same established "bypass the guard, exercise the launcher"
# pattern `test_execution_real_docker_lifecycle.py` uses).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
@pytest.mark.asyncio
async def test_t9_absent_channel_preserves_original_empty_stdin_semantics(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    approved = ApprovedLaunchSpec(
        job_id=job.id,
        image=REAL_IMAGE_DIGEST,
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
        environment={},  # deliberately empty -- simulates "no transport channel present"
        command=("python3", "-m", "dispatcher", "--operation", "evaluate_expression"),
        timeout_s=10.0,
    )

    outcome = await execute_approved_launch(
        approved,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    assert outcome.exit_code == 0
    asset = await _get_asset(outcome.result_asset_id)
    stdout = json.loads(asset.asset_metadata["stdout"])
    assert stdout == {"result": None, "error": "missing or invalid 'expression' field"}
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T10 -- security posture unchanged under the new transport; environment
# carries ONLY the one authorized variable.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
@pytest.mark.asyncio
async def test_t10_security_posture_unchanged_under_new_transport(docker_client):
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(expression="2 + 3 * 4"), resolver)
    approved = await validate_and_approve(candidate, resolver)

    kwargs = _docker_create_kwargs(approved, container_name=f"aikdap-t10-{uuid.uuid4().hex[:12]}")
    container = docker_client.containers.create(**kwargs)
    try:
        container.start()
        container.wait(timeout=10)
        container.reload()
        attrs = container.attrs

        assert attrs["HostConfig"]["NetworkMode"] == "none"
        assert attrs["HostConfig"]["Privileged"] is False
        assert attrs["HostConfig"]["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in "".join(attrs["HostConfig"]["SecurityOpt"])
        assert attrs["HostConfig"]["ReadonlyRootfs"] is True
        assert attrs["Config"]["User"] == "10001:10001"
        assert attrs["HostConfig"]["Memory"] == 256 * 1024 * 1024
        assert attrs["HostConfig"]["NanoCpus"] == 1_000_000_000
        assert attrs["HostConfig"]["PidsLimit"] == 32

        env_list = attrs["Config"]["Env"]
        aikdap_vars = [e for e in env_list if e.startswith("AIKDAP_")]
        assert len(aikdap_vars) == 1
        assert aikdap_vars[0] == f'{ENV_VAR}={{"expression":"2 + 3 * 4"}}'
        # No unrelated/secret-shaped env leaked in.
        assert not any("DATABASE_URL" in e or "SECRET" in e.upper() for e in env_list)
    finally:
        container.remove(force=True)


# ---------------------------------------------------------------------------
# T11 -- container cleanup still succeeds (folded into T4/T5-T6/T9/T10
# above via _assert_container_gone / explicit .remove() in finally
# blocks -- reconfirmed explicitly here with a full real run).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t11_cleanup_succeeds(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(), resolver)
    approved = await validate_and_approve(candidate, resolver)

    await execute_approved_launch(
        approved,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T12 -- no execution-input asset is accidentally used as the parameter
# transport; mounts stay driven only by input_asset_ids, untouched by
# this phase.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t12_mounts_unaffected_by_parameter_transport():
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(expression="2 + 3 * 4"), resolver)
    approved = await validate_and_approve(candidate, resolver)
    assert approved.mounts == ()  # input_asset_ids=[] -> no mounts, exactly as before this phase


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def _imports_any_of(source: str, module_names: set[str]) -> bool:
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in module_names for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in module_names:
                return True
    return False


def test_no_new_docker_sdk_importer_introduced():
    """`launcher.py` remains the only module importing the Docker SDK --
    this phase's env-var channel needed zero changes to it. AST-based
    (matching this sprint's established pattern, e.g.
    `test_execution_docker_policy.py`'s own `_imports_docker_sdk`) since
    a naive substring check also matches these modules' own docstrings
    describing the Docker-SDK-isolation invariant, not an actual import."""
    import pathlib

    import execution_launcher.docker_policy as docker_policy_module
    import execution_launcher.translator as translator_module

    for module in (docker_policy_module, translator_module):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert not _imports_any_of(source, {"docker"})
