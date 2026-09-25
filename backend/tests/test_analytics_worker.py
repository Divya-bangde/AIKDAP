"""Milestone 6 Task 6: `workers.tasks._run_analysis` -- the Celery task
body that runs the analytics LangGraph for one pending analysis asset.

Builds a real dataset asset backed by `LocalStorageProvider` (in a tmp
dir) and a pending analysis asset, monkeypatches the analytics graph's
storage/LLM dependencies (`app.agents.analytics.nodes.get_storage_provider`
/ `get_llm_gateway`), and runs `_run_analysis` end to end against the
real test database -- same rationale as `test_report_generation_
reconciliation.py`: this exercises real persistence, not a mock.
"""

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.storage import LocalStorageProvider
from app.modules.research.repository import ResearchStepRepository
from app.workers import tasks as tasks_module

CSV = b"region,revenue\nWest,100\nEast,300\nWest,50\n"
GOOD_PLAN = {
    "group_by": ["region"],
    "metrics": [{"column": "revenue", "agg": "sum", "alias": "total"}],
    "sort": {"by": "total"},
    "chart": {"type": "bar", "x": "region", "y": ["total"]},
}


class ScriptedGateway:
    """Copied from `test_analytics_graph.py`: returns the queued
    responses in order; an Exception entry is raised."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, model="fake", provider="fake", latency_ms=1)


async def _make_dataset_asset(session, project, storage: LocalStorageProvider) -> Asset:
    storage_path = await storage.save(project_id=project.id, filename="data.csv", content=CSV)
    asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Revenue Data",
        description=None,
        asset_type=AssetType.DATASET,
        status=AssetStatus.ACTIVE,
        mime_type="text/csv",
        file_name="data.csv",
        file_extension=".csv",
        file_size=len(CSV),
        storage_path=storage_path,
        checksum=uuid.uuid4().hex,
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.COMPLETED,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def _make_analysis_asset(session, project, *, dataset_id: uuid.UUID, sheet: str | None = None) -> Asset:
    asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Revenue by region?",
        description=None,
        asset_type=AssetType.CHART,
        status=AssetStatus.ACTIVE,
        mime_type="application/json",
        file_name="analysis.json",
        file_extension="json",
        file_size=0,
        storage_path="",
        checksum="",
        source=AssetSource.GENERATED,
        version=1,
        tags=["analysis", f"dataset:{dataset_id}"],
        asset_metadata={
            "analysis": {
                "dataset_id": str(dataset_id),
                "question": "Revenue by region?",
                "sheet": sheet,
                "history": [],
                "plan": None,
                "result": None,
                "narrative": None,
                "unverified_numbers": [],
            }
        },
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.PENDING,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_run_analysis_completes_and_persists_result_and_profile(session, project, monkeypatch, tmp_path):
    storage = LocalStorageProvider(base_dir=Path(tmp_path))
    monkeypatch.setattr("app.agents.analytics.nodes.get_storage_provider", lambda: storage)
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East leads with 300, West has 150.")
    monkeypatch.setattr("app.agents.analytics.nodes.get_llm_gateway", lambda: gateway)

    dataset = await _make_dataset_asset(session, project, storage)
    analysis = await _make_analysis_asset(session, project, dataset_id=dataset.id)

    result = await tasks_module._run_analysis(analysis.id)

    assert result["status"] == "ok"
    await session.refresh(analysis)
    await session.refresh(dataset)
    assert analysis.processing_status is AssetProcessingStatus.COMPLETED
    assert analysis.asset_metadata["analysis"]["result"]["rows"] == [
        {"region": "East", "total": 300},
        {"region": "West", "total": 150},
    ]
    assert dataset.asset_metadata["profile"]["row_count"] == 3

    steps = await ResearchStepRepository(session).list_by_asset(analysis.id)
    assert len(steps) == 5


@pytest.mark.asyncio
async def test_run_analysis_with_non_default_sheet_does_not_write_dataset_profile_cache(
    session, project, monkeypatch, tmp_path
):
    """R5 item 1: `get_profile` treats `dataset.asset_metadata["profile"]` as the
    DEFAULT sheet's cached profile. An analysis with `sheet` set must not write it,
    even though `read_dataframe` ignores `sheet` for CSV -- the cache write is
    gated on `analysis["sheet"]`, not on whether the sheet actually mattered."""
    storage = LocalStorageProvider(base_dir=Path(tmp_path))
    monkeypatch.setattr("app.agents.analytics.nodes.get_storage_provider", lambda: storage)
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East leads with 300, West has 150.")
    monkeypatch.setattr("app.agents.analytics.nodes.get_llm_gateway", lambda: gateway)

    dataset = await _make_dataset_asset(session, project, storage)
    analysis = await _make_analysis_asset(session, project, dataset_id=dataset.id, sheet="Other")

    result = await tasks_module._run_analysis(analysis.id)

    assert result["status"] == "ok"
    await session.refresh(dataset)
    assert "profile" not in dataset.asset_metadata


@pytest.mark.asyncio
async def test_run_analysis_fails_with_scrubbed_error_and_skipped_steps(session, project, monkeypatch, tmp_path):
    storage = LocalStorageProvider(base_dir=Path(tmp_path))
    monkeypatch.setattr("app.agents.analytics.nodes.get_storage_provider", lambda: storage)

    bad_plan = json.dumps({**GOOD_PLAN, "group_by": ["regoin"]})
    gateway = ScriptedGateway(bad_plan, bad_plan)
    monkeypatch.setattr("app.agents.analytics.nodes.get_llm_gateway", lambda: gateway)

    dataset = await _make_dataset_asset(session, project, storage)
    analysis = await _make_analysis_asset(session, project, dataset_id=dataset.id)

    await tasks_module._run_analysis(analysis.id)

    await session.refresh(analysis)
    assert analysis.processing_status is AssetProcessingStatus.FAILED
    # AnalysisPlanError is ours (names columns, not secrets) -- surfaced
    # verbatim, not scrubbed via `scrub_report_error`.
    assert analysis.processing_error is not None
    assert "Could not build a valid analysis plan" in analysis.processing_error

    steps = await ResearchStepRepository(session).list_by_asset(analysis.id)
    statuses = {step.node_name: step.status.value for step in steps}
    assert "skipped" in statuses.values()


@pytest.mark.asyncio
async def test_run_analysis_with_malformed_metadata_fails_instead_of_staying_running(
    session, project, monkeypatch, tmp_path
):
    """Fix round 1: `asset_metadata["analysis"]` missing `dataset_id`
    (KeyError) must take the failure path, not escape `_run_analysis`
    and leave the asset stuck at RUNNING."""
    storage = LocalStorageProvider(base_dir=Path(tmp_path))
    monkeypatch.setattr("app.agents.analytics.nodes.get_storage_provider", lambda: storage)
    monkeypatch.setattr("app.agents.analytics.nodes.get_llm_gateway", lambda: ScriptedGateway())

    asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Broken analysis",
        description=None,
        asset_type=AssetType.CHART,
        status=AssetStatus.ACTIVE,
        mime_type="application/json",
        file_name="analysis.json",
        file_extension="json",
        file_size=0,
        storage_path="",
        checksum="",
        source=AssetSource.GENERATED,
        version=1,
        tags=["analysis"],
        asset_metadata={"analysis": {"question": "Revenue by region?"}},  # no dataset_id
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.PENDING,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)

    result = await tasks_module._run_analysis(asset.id)

    assert result["status"] == "ok"
    await session.refresh(asset)
    assert asset.processing_status is AssetProcessingStatus.FAILED
    assert asset.processing_error is not None
