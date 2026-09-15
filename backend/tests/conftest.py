"""Shared pytest fixtures.

The integration tests here run against the real Postgres instance the
application uses, not a mock: the bug under test was a data-propagation
failure that only shows up once state has actually been persisted, so
verifying it against an in-memory double would prove nothing.

Every fixture cleans up after itself — the user cascade removes the
project, runs, steps, and messages it created.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

# Registers every ORM model on `Base.metadata`. Without it the test
# process cannot resolve the users/projects foreign keys, exactly as a
# worker process could not.
import app.workers.celery_app  # noqa: F401
from app.core.config import settings
from app.core.llm.provider_health import get_provider_health_registry
from app.database.session import async_session_factory
from app.modules.auth.models import User
from app.modules.projects.models import Project, ProjectStatus, ProjectType
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset


@pytest.fixture(autouse=True)
def isolated_provider_health():
    """Clear the LLM provider breaker around every test (Sprint 9G).

    The registry is a process-wide singleton, so without this a test
    that provokes a 503 would leave Gemini marked unavailable and the
    *next* test's call would be short-circuited into a fallback it
    never asked for. That failure mode is order-dependent and
    miserable to diagnose, and it is entirely avoidable here.
    """
    registry = get_provider_health_registry()
    registry.reset()
    yield
    registry.reset()


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


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    """An `AsyncSession` bound to the real application database."""
    async with async_session_factory() as db_session:
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
