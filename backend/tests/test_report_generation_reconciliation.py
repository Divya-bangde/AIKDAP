"""Final-review finding I2: stale `Asset` (report) reconciliation.

Root problem, mirroring Sprint 9J's `ResearchRun` finding:
`workers.tasks._generate_report` sets `processing_status=RUNNING`
before invoking the report LangGraph, and only reaches its own success
or `except` path if the worker process survives long enough to run it.
A worker crash mid-generation (or before the task even starts) leaves
the asset at `running`/`pending` forever -- nothing in the architecture
ever revisits the row. `reconcile_stale_report_generations()` is the
recovery mechanism; these tests run against the real Postgres test
database (see `conftest.py`), matching
`test_research_run_reconciliation.py`'s own reasoning for doing the
same.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.config import settings
from app.database.session import engine
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.workers.reconciliation import (
    STALE_REPORT_FAILURE_REASON,
    reconcile_stale_report_generations,
)


@pytest_asyncio.fixture(autouse=True)
async def clean_ambient_stale_reports():
    """Same rationale as `test_research_run_reconciliation.clean_ambient_stale_runs`:
    this suite runs against the real database, which can genuinely
    contain stale rows from outside this test module. Reconciling once
    up front gives every test a known-clean baseline."""
    await engine.dispose()
    await reconcile_stale_report_generations()


async def _make_report_asset(
    session,
    project,
    *,
    updated_at: datetime,
    processing_status: AssetProcessingStatus = AssetProcessingStatus.RUNNING,
    asset_type: AssetType = AssetType.SUMMARY,
    source: AssetSource = AssetSource.GENERATED,
) -> Asset:
    asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Study Summary",
        description=None,
        asset_type=asset_type,
        status=AssetStatus.ACTIVE,
        mime_type="application/json",
        file_name="study-summary.json",
        file_extension="json",
        file_size=0,
        storage_path="",
        checksum=uuid.uuid4().hex,
        source=source,
        version=1,
        tags=[],
        asset_metadata={"kind": "study_summary", "sections": []},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=processing_status,
    )
    session.add(asset)
    await session.flush()
    await session.commit()
    # `updated_at` carries `onupdate=func.now()` -- a plain INSERT sets it
    # to "now", so backdating it requires a real UPDATE after the fact,
    # the same approach `test_execution_job_reconciliation.py` uses for
    # its own `updated_at`-driven staleness query.
    await session.execute(
        Asset.__table__.update().where(Asset.id == asset.id).values(updated_at=updated_at)
    )
    await session.commit()
    await session.refresh(asset)
    return asset


def _stale_timestamp() -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        seconds=settings.report_generation_stale_after_seconds + 60
    )


def _fresh_timestamp() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_a_stale_running_report_is_marked_failed_with_a_safe_reason(session, project):
    asset = await _make_report_asset(session, project, updated_at=_stale_timestamp())

    reconciled_count = await reconcile_stale_report_generations()

    await session.refresh(asset)
    assert reconciled_count == 1
    assert asset.processing_status is AssetProcessingStatus.FAILED
    assert asset.processing_error == STALE_REPORT_FAILURE_REASON


@pytest.mark.asyncio
async def test_a_stale_pending_report_is_also_marked_failed(session, project):
    """A worker that died BEFORE the task even started leaves the asset
    at `pending`, not `running` -- both must be reconciled."""
    asset = await _make_report_asset(
        session, project, updated_at=_stale_timestamp(), processing_status=AssetProcessingStatus.PENDING
    )

    reconciled_count = await reconcile_stale_report_generations()

    await session.refresh(asset)
    assert reconciled_count == 1
    assert asset.processing_status is AssetProcessingStatus.FAILED


@pytest.mark.asyncio
async def test_a_freshly_running_report_is_left_alone(session, project):
    asset = await _make_report_asset(session, project, updated_at=_fresh_timestamp())

    reconciled_count = await reconcile_stale_report_generations()

    await session.refresh(asset)
    assert reconciled_count == 0
    assert asset.processing_status is AssetProcessingStatus.RUNNING


@pytest.mark.asyncio
async def test_completed_and_failed_reports_are_never_touched_regardless_of_age(session, project):
    old_completed = await _make_report_asset(
        session, project, updated_at=_stale_timestamp(), processing_status=AssetProcessingStatus.COMPLETED
    )
    old_failed = await _make_report_asset(
        session, project, updated_at=_stale_timestamp(), processing_status=AssetProcessingStatus.FAILED
    )

    reconciled_count = await reconcile_stale_report_generations()

    await session.refresh(old_completed)
    await session.refresh(old_failed)
    assert reconciled_count == 0
    assert old_completed.processing_status is AssetProcessingStatus.COMPLETED
    assert old_failed.processing_status is AssetProcessingStatus.FAILED


@pytest.mark.asyncio
async def test_reconciliation_only_touches_generated_report_assets_among_several(session, project):
    stale_report = await _make_report_asset(session, project, updated_at=_stale_timestamp())
    stale_upload = await _make_report_asset(
        session,
        project,
        updated_at=_stale_timestamp(),
        asset_type=AssetType.DOCUMENT,
        source=AssetSource.UPLOAD,
    )

    reconciled_count = await reconcile_stale_report_generations()

    await session.refresh(stale_report)
    await session.refresh(stale_upload)
    assert reconciled_count == 1
    assert stale_report.processing_status is AssetProcessingStatus.FAILED
    assert stale_upload.processing_status is AssetProcessingStatus.RUNNING


@pytest.mark.asyncio
async def test_reconciling_twice_only_mutates_once(session, project):
    asset = await _make_report_asset(session, project, updated_at=_stale_timestamp())

    first_pass = await reconcile_stale_report_generations()
    second_pass = await reconcile_stale_report_generations()

    await session.refresh(asset)
    assert first_pass == 1
    assert second_pass == 0
    assert asset.processing_status is AssetProcessingStatus.FAILED
