"""Sprint 16 Phase 7B.26 -- the real, approved CLASS_EXPR execution image.

Builds on Phase 7B.25's real launcher (unchanged this phase) by proving
the one thing that was missing: a real, built, digest-pinned Docker
image that `execution_launcher/docker_policy.py::_APPROVED_IMAGE_DIGESTS`
now actually approves for `ResourceClass.CLASS_EXPR`
(`execution_images/class_expr/`, dispatcher module invented this phase
-- see that module's own docstring for its stdin/stdout contract).

Every test here runs against REAL Postgres and the REAL Docker daemon,
using the REAL image this phase built (`aikdap-exec-class-expr@sha256:
...`) -- no fake image, no mocked docker-py call, matching the same
"mocks only for narrow error isolation" discipline `test_execution_
real_docker_lifecycle.py` established.

T6/T7 (real front door, "2 + 3 * 4 -> 14 exactly") are included but are
EXPECTED TO NOT reach 14 -- this phase's own investigation (see the
report's Executive Verdict / section on the parameter-passing gap)
found that `LaunchRequest.parameters` (which DOES carry
`{"expression": "2 + 3 * 4", ...}`, confirmed by reading
`experiment_service.py:600` and `execution/service.py:219`) is never
read by `docker_policy.py`'s `_expected_environment()`/
`_DISPATCH_COMMAND`/`_expected_mounts()` -- so no channel exists,
using only files this phase is authorized to touch (docker_policy.py's
checks/constants are locked except the one digest line; translator.py
and launcher.py are fully locked), to deliver the expression into the
container. These two tests capture that REAL, honest outcome (the job
still reaches a durable terminal state; the dispatcher reports a
normal "missing expression" result, not a crash) rather than skip or
paper over it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import docker
import docker.errors
import pytest
import pytest_asyncio

from app.database.session import async_session_factory, engine
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.auth.models import User
from app.modules.execution.enums import ExecutionAttemptStatus, ExecutionJobStatus
from app.modules.execution.models import ExecutionAttempt, ExecutionJob
from app.modules.execution.service import container_name_for_attempt
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from execution_launcher import docker_policy
from execution_launcher.docker_policy import validate_and_approve
from execution_launcher.launcher import execute_approved_launch
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

#: The real digest this phase built and approved -- read from the
#: catalog itself (not re-typed) so this file breaks loudly if the
#: catalog entry is ever edited out from under it.
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
        email=f"pytest-classexpr-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Class Expr Image Test User",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    project = Project(
        owner_id=user.id,
        name=name,
        description="Created by test_execution_scientific_image_class_expr.py",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def project(session) -> AsyncIterator[Project]:
    proj = await _make_owner_and_project(session, name="Class Expr Image Test Project")
    yield proj
    user = await session.get(User, proj.owner_id)
    if user is not None:
        await session.delete(user)
        await session.commit()


def _expr_request(*, expression: str = "2 + 3 * 4") -> LaunchRequest:
    return LaunchRequest(
        job_id=uuid.uuid4(),
        experiment_plan_id=uuid.uuid4(),
        experiment_plan_version=1,
        owner_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        capability=ExecutionCapability.EVALUATE_EXPRESSION,
        operation=ExecutionOperation.EVALUATE_EXPRESSION,
        parameters={"expression": expression},
        resource_class=ResourceClass.CLASS_EXPR,
        input_asset_ids=[],
    )


async def _make_job_and_attempt(session, project: Project) -> tuple[ExecutionJob, ExecutionAttempt]:
    job = ExecutionJob(
        project_id=project.id,
        owner_id=project.owner_id,
        experiment_plan_id=uuid.uuid4(),
        experiment_plan_version=1,
        capability=ExecutionCapability.EVALUATE_EXPRESSION,
        operation=ExecutionOperation.EVALUATE_EXPRESSION,
        resource_class=ResourceClass.CLASS_EXPR,
        parameters={"expression": "2 + 3 * 4"},
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
# T3 -- catalog / guard accepts the real digest, end-to-end through the
# real (unmodified) build_candidate() -> validate_and_approve() pipeline.
# ---------------------------------------------------------------------------


def test_t3_catalog_holds_real_digest_not_a_placeholder():
    assert "TEST-ONLY-NOT-A-REAL-IMAGE" not in REAL_IMAGE_DIGEST
    assert REAL_IMAGE_DIGEST.startswith("aikdap-exec-class-expr@sha256:")
    assert len(REAL_IMAGE_DIGEST.split("sha256:")[1]) == 64


@pytest.mark.asyncio
async def test_t3_guard_approves_real_image_for_class_expr():
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(), resolver)
    assert candidate.image == REAL_IMAGE_DIGEST

    approved = await validate_and_approve(candidate, resolver)
    assert isinstance(approved, ApprovedLaunchSpec)
    assert approved.image == REAL_IMAGE_DIGEST
    assert approved.command == ("python3", "-m", "dispatcher", "--operation", "evaluate_expression")


# ---------------------------------------------------------------------------
# T4 -- guard rejects a non-approved digest.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_guard_rejects_non_approved_digest():
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(), resolver)
    from dataclasses import replace

    tampered = replace(candidate, image="aikdap-exec-class-expr@sha256:" + "0" * 64)

    with pytest.raises(SecurityBlocked) as exc_info:
        await validate_and_approve(tampered, resolver)
    assert exc_info.value.check_name == "image_mismatch"


# ---------------------------------------------------------------------------
# T5 / T8 / T9 / T10 / T11 / T12 / T14 -- real launcher creates the real
# container from the real approved spec, runs it to completion, persists
# the result, and cleans up. One test covers all of these together since
# they are one causal chain (the same real run), each asserted in turn.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
@pytest.mark.asyncio
async def test_t5_through_t14_real_launch_of_the_real_image(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(), resolver)
    approved = await validate_and_approve(candidate, resolver)
    assert approved.image == REAL_IMAGE_DIGEST  # T3 reconfirmed inline

    outcome = await execute_approved_launch(
        approved,
        attempt_id=attempt.id,
        container_name=attempt.container_name,
        project_id=project.id,
        owner_id=project.owner_id,
        job_id=job.id,
        session=session,
    )

    # T5 -- the real launcher genuinely created and ran the real image.
    assert outcome.exit_code == 0

    # T8 -- container_id was persisted.
    reloaded = await _reload_attempt(attempt.id)
    assert reloaded.container_id is not None

    # T9 -- lifecycle reached EXITED (PENDING_CREATE -> CREATED -> RUNNING -> EXITED).
    assert reloaded.status == ExecutionAttemptStatus.EXITED
    assert reloaded.started_at is not None
    assert reloaded.completed_at is not None

    # T10 -- result persisted via the existing Asset/StorageProvider mechanism.
    assert outcome.result_asset_id is not None
    asset = await _get_asset(outcome.result_asset_id)
    assert asset.asset_metadata["exit_code"] == 0
    # Sprint 16 Phase 7B.27: `_expr_request()` carries a real expression
    # ("2 + 3 * 4") and `approved.environment` now genuinely delivers it
    # (see execution_launcher/docker_policy.py::_expected_class_expr_environment)
    # -- the dispatcher receives it and evaluates it for real, unlike
    # 7B.26 where this same code path produced "missing or invalid
    # 'expression' field".
    assert '"result": 14.0' in asset.asset_metadata["stdout"]

    # T11 / T12 -- cleanup succeeded, no container remains.
    _assert_container_gone(docker_client, attempt.container_name)


# ---------------------------------------------------------------------------
# T14 -- security/resource posture on THIS image specifically, via real
# `docker inspect` while it is briefly running (not source review).
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker_image("aikdap-exec-class-expr")
@pytest.mark.asyncio
async def test_t14_security_posture_on_real_container(session, project, docker_client):
    job, attempt = await _make_job_and_attempt(session, project)
    resolver = StaticInputResolver()
    candidate = await build_candidate(_expr_request(), resolver)
    approved = await validate_and_approve(candidate, resolver)

    from execution_launcher.launcher import _docker_create_kwargs

    kwargs = _docker_create_kwargs(approved, container_name=f"{attempt.container_name}-posture")
    container = docker_client.containers.create(**kwargs)
    try:
        container.start()
        container.wait()
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
        assert "/tmp" in attrs["HostConfig"]["Tmpfs"]
    finally:
        container.remove(force=True)


# ---------------------------------------------------------------------------
# T6/T7 (7B.26) -- REMOVED in Sprint 16 Phase 7B.27. This slice's own
# test used to document that the real front door reached EXITED but
# produced "missing or invalid 'expression' field" instead of 14 -- the
# exact parameter-passing gap 7B.27 closes (see
# execution_launcher/docker_policy.py::_expected_class_expr_environment
# and tests/test_execution_class_expr_parameter_transport.py, whose own
# T6/T7 now prove the SAME real front door produces 14). Kept removed
# rather than left both stale AND duplicated across two files.
# ---------------------------------------------------------------------------
