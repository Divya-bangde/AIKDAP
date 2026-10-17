"""Offline by default; integration requires explicit disposable service URLs.

Run ``python scripts/check.py integration`` to provision disposable PostgreSQL
and Redis, migrate, and run the suite. Normal pytest never uses application
credentials or the developer's .env, and unexpected network calls fail closed.
"""

import asyncio
import os
import shutil
import socket
import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from pydantic_settings import DotEnvSettingsSource, EnvSettingsSource

# LiteLLM otherwise fetches its price catalogue at import time, before fixtures
# can install the network guard. Use its bundled catalogue for repeatable tests.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


def _test_service_url(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    parsed = urlsplit(value)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise pytest.UsageError(f"{name} must point to a local disposable service")
    if name == "TEST_DATABASE_URL" and not parsed.path.startswith("/aikdap_test"):
        raise pytest.UsageError("TEST_DATABASE_URL database name must start with aikdap_test")
    if name == "TEST_DATABASE_URL" and "connect_timeout=" not in parsed.query:
        value += ("&" if parsed.query else "?") + "connect_timeout=5"
    return value


TEST_DATABASE_URL = _test_service_url(
    "TEST_DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1:1/aikdap_test"
)
TEST_REDIS_URL = _test_service_url("TEST_REDIS_URL", "redis://127.0.0.1:1/0")
_test_settings = {
    "secret_key": "isolated-test-key-never-use-in-production",
    "database_url": TEST_DATABASE_URL,
    "celery_broker_url": TEST_REDIS_URL,
    "celery_result_backend": TEST_REDIS_URL,
}

# Construct the singleton from repository defaults and explicit test endpoints,
# before importing any module that captures it. No .env or ambient API key leaks
# into a supposedly offline test. Restore source classes immediately afterward.
with (
    patch.object(EnvSettingsSource, "__call__", return_value=_test_settings),
    patch.object(DotEnvSettingsSource, "__call__", return_value={}),
):
    from app.core.config import Settings, settings

# Tests mix async tests (one fresh loop each) with sync tests that drive
# Celery tasks through their own `asyncio.run()`, exactly like the worker.
# Use the worker's NullPool engine so no connection outlives its loop.
# Must run before anything below imports `async_session_factory` by name.
from app.database.session import configure_for_worker_process

configure_for_worker_process()

# Registers every ORM model on `Base.metadata`. Without it the test
# process cannot resolve the users/projects foreign keys, exactly as a
# worker process could not.
import app.workers.celery_app  # noqa: F401
from app.core.llm import provider_health
from app.database.session import async_session_factory
from app.modules.auth.models import User
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset


def _local_docker_images() -> set[str] | None:
    """Every `repo` and `repo:tag` present locally, or None when the
    Docker daemon is unreachable."""
    try:
        import docker

        images = docker.from_env(timeout=3).images.list()
    except Exception:
        return None
    names: set[str] = set()
    for image in images:
        for tag in image.tags:
            names.update((tag, tag.rsplit(":", 1)[0]))
        names.update(digest.split("@", 1)[0] for digest in image.attrs.get("RepoDigests", []))
    return names


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip tests whose external dependency is absent on this machine:
    the tesseract binary (ships in the backend Docker image) and the
    images real-container execution tests launch."""
    for item in items:
        database_test = "session" in item.fixturenames or "async_session_factory" in vars(item.module)
        if database_test or "redis_registry" in item.fixturenames:
            item.add_marker(pytest.mark.integration)
        if database_test and not os.environ.get("TEST_DATABASE_URL"):
            item.add_marker(pytest.mark.skip(reason="requires disposable PostgreSQL via TEST_DATABASE_URL"))
        if item.get_closest_marker("live_ollama") and not os.environ.get("TEST_LIVE_OLLAMA"):
            item.add_marker(pytest.mark.skip(reason="live Ollama is opt-in via TEST_LIVE_OLLAMA=1"))

    if not shutil.which("tesseract"):
        skip = pytest.mark.skip(reason="tesseract OCR binary not on PATH")
        for item in items:
            if "requires_tesseract" in item.keywords:
                item.add_marker(skip)

    image_tests = [item for item in items if item.get_closest_marker("requires_docker_image")]
    if not image_tests:
        return
    available = _local_docker_images()
    for item in image_tests:
        image = item.get_closest_marker("requires_docker_image").args[0]
        if available is None:
            item.add_marker(pytest.mark.skip(reason="Docker daemon unreachable"))
        elif image not in available:
            item.add_marker(pytest.mark.skip(reason=f"Docker image {image} not present locally"))


@pytest.fixture(autouse=True)
def isolated_settings_and_network(monkeypatch):
    """Restore mutable settings and deny all non-test network connections."""
    original_settings = settings.model_copy(deep=True)
    for key in list(os.environ):
        if key.lower() in Settings.model_fields:
            monkeypatch.delenv(key)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    creating_socketpair = False
    allowed = set()
    for name, value in (("TEST_DATABASE_URL", TEST_DATABASE_URL), ("TEST_REDIS_URL", TEST_REDIS_URL)):
        if os.environ.get(name):
            parsed = urlsplit(value)
            allowed.add((parsed.hostname, parsed.port or (5432 if "DATABASE" in name else 6379)))
    if os.environ.get("TEST_LIVE_OLLAMA"):
        allowed.add(("127.0.0.1", 11434))

    def guarded_connect(sock, address):
        if not creating_socketpair and sock.family in {socket.AF_INET, socket.AF_INET6}:
            host, port = address[:2]
            if (host, port) not in allowed:
                raise AssertionError(f"Unexpected network call in test to {host}:{port}; inject a transport or use disposable services")
        return original_connect(sock, address)

    def local_socketpair(*args, **kwargs):
        # Windows implements asyncio's internal wakeup socketpair with a local
        # TCP connection. It is IPC, not an application network request.
        nonlocal creating_socketpair
        creating_socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            creating_socketpair = False

    monkeypatch.setattr(socket, "socketpair", local_socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(provider_health, "_registry", provider_health.ProviderHealthRegistry())
    yield
    for field in Settings.model_fields:
        setattr(settings, field, getattr(original_settings, field))


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch):
    """Remove retry backoff for the whole suite (Sprint 9G).

    The gateway retries transient failures with a jittered 0.5-4s
    backoff, which is right in production and intolerable in a unit
    suite — a handful of error-path tests would add minutes of pure
    sleeping.

    This zeroes the *delay*, never the retry count or the
    classification: how many attempts happen, and which errors earn
    them, is exactly what the tests are checking, so those stay real.
    `test_llm_resilience.py` covers the delay schedule itself directly
    against `_retry_delay`, where it can be asserted without waiting.
    """
    monkeypatch.setattr(settings, "llm_retry_base_delay", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 0.0)


@pytest.fixture
def subprocess_test_environment():
    """Pass disposable endpoints explicitly to independently launched workers.

    The isolation guard clears ambient application settings. A subprocess does
    not inherit the parent interpreter's settings singleton or pytest fixtures.
    """
    child_env = os.environ.copy()
    child_env.update(
        SECRET_KEY=settings.secret_key,
        DATABASE_URL=TEST_DATABASE_URL,
        CELERY_BROKER_URL=TEST_REDIS_URL,
        CELERY_RESULT_BACKEND=TEST_REDIS_URL,
        LITELLM_LOCAL_MODEL_COST_MAP="True",
    )
    return child_env


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    """Real disposable PostgreSQL; unreachable databases fail within 5 seconds."""
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("requires disposable PostgreSQL via TEST_DATABASE_URL; run scripts/check.py integration")
    async with async_session_factory() as db_session:
        try:
            await asyncio.wait_for(db_session.connection(), timeout=5)
        except Exception:
            pytest.fail("Disposable PostgreSQL unavailable (5-second connection bound)", pytrace=False)
        yield db_session


@pytest_asyncio.fixture
async def project(session) -> AsyncIterator[Project]:
    """A throwaway user and project, removed after the test."""
    user = User(
        email=f"pytest-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Citation Test User",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    owned_project = Project(
        owner_id=user.id,
        name="Citation propagation test project",
        description="Created by the test suite.",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(owned_project)
    await session.flush()
    await session.commit()

    yield owned_project

    # Cascades to projects -> research_runs -> steps/messages.
    await session.delete(user)
    await session.commit()


@pytest.fixture
def research_query() -> str:
    """The query used across the citation tests."""
    return (
        "Analyze the current Indian poultry industry. Identify major market "
        "trends, key challenges, and emerging opportunities."
    )


@pytest.fixture
def make_report_asset(session):
    """Factory for a committed GENERATED report asset (Milestone 10
    step 4), in the exact shape `ReportService.generate_synopsis`
    creates. Removed with its owner by `project`'s user cascade."""

    async def _make(
        project: Project,
        *,
        status: AssetProcessingStatus = AssetProcessingStatus.PENDING,
        kind: str = "study_summary",
        processing_error: str | None = None,
        sections: list[dict] | None = None,
    ) -> Asset:
        asset = Asset(
            project_id=project.id,
            owner_id=project.owner_id,
            title="Study Summary" if kind == "study_summary" else "Project Synopsis",
            description=None,
            asset_type=AssetType.SUMMARY if kind == "study_summary" else AssetType.REPORT,
            status=AssetStatus.ACTIVE,
            mime_type="application/json",
            file_name="study-summary.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            asset_metadata={"kind": kind, "sections": sections or []},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=project.owner_id,
            processing_status=status,
            processing_error=processing_error,
        )
        session.add(asset)
        await session.commit()
        return asset

    return _make
