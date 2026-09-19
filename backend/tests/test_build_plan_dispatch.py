"""Pins the `kind`-dispatch branch inside `_generate_report` (Task 6
fix round 1): the only wire connecting Task 5's endpoint to Tasks 1-4's
build-plan graph. Follows `test_build_plan_routes.py` for DB setup and
cleanup; uses the real Postgres in Docker. No LLM call and no real
graph execution -- the graph getters are monkeypatched with spies.
"""

import uuid

import pytest
from sqlalchemy import delete

from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.database.session import async_session_factory
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.auth.models import User
from app.modules.projects.models import Project
from app.workers.tasks import _generate_report

PASSWORD_TITLE = "Dispatch test asset"


class _FakeGraph:
    """Records the inputs and config it was invoked with, and returns a
    fixed result -- standing in for a real LangGraph `CompiledGraph`."""

    def __init__(self, sections: list[dict]) -> None:
        self.sections = sections
        self.calls: list[dict] = []

    async def ainvoke(self, inputs: dict, config: dict) -> dict:
        self.calls.append({"inputs": inputs, "config": config})
        return {"sections": self.sections}


async def _make_report_asset(*, kind: str, extra_metadata: dict) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Insert one GENERATED/REPORT asset directly, owned by a fresh user
    and a throwaway project (deleting the user cascades both)."""
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    async with async_session_factory() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        project = Project(owner_id=user.id, name="Dispatch test project")
        session.add(project)
        await session.commit()
        await session.refresh(project)

        asset = Asset(
            project_id=project.id,
            owner_id=user.id,
            title=PASSWORD_TITLE,
            description=None,
            asset_type=AssetType.REPORT,
            status=AssetStatus.ACTIVE,
            mime_type="application/json",
            file_name="report.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="dispatch-test",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            asset_metadata={"kind": kind, "sections": [], **extra_metadata},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=user.id,
            processing_status=AssetProcessingStatus.PENDING,
        )
        session.add(asset)
        await session.commit()
        return asset.id, user.id, email


async def _cleanup(email: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email == email))
        await session.commit()


@pytest.mark.asyncio
async def test_a_build_plan_asset_routes_to_the_build_plan_graph(monkeypatch):
    asset_id, _, email = await _make_report_asset(
        kind="build_plan", extra_metadata={"asset_ids": ["doc-1", "doc-2"]}
    )
    try:
        build_plan_graph = _FakeGraph([{"title": "s", "content": "c", "covered": True, "citations": []}])
        synopsis_graph = _FakeGraph([{"title": "should-not-run", "content": "", "covered": True, "citations": []}])
        monkeypatch.setattr("app.workers.tasks.get_build_plan_graph", lambda: build_plan_graph)
        monkeypatch.setattr("app.workers.tasks.get_report_graph", lambda: synopsis_graph)

        result = await _generate_report(asset_id)

        assert result == {"status": "ok", "asset_id": str(asset_id)}
        assert len(build_plan_graph.calls) == 1
        assert synopsis_graph.calls == []

        call = build_plan_graph.calls[0]
        inputs = call["inputs"]
        assert inputs["asset_ids"] == ["doc-1", "doc-2"]
        assert "kind" not in inputs

        tracker = call["config"]["configurable"]["tracker"]
        assert set(tracker._registry) == set(BUILD_PLAN_AGENT_REGISTRY)

        async with async_session_factory() as session:
            asset = await AssetRepository(session).get_by_id(asset_id)
            assert asset.processing_status == AssetProcessingStatus.COMPLETED
            assert asset.asset_metadata["sections"] == build_plan_graph.sections
    finally:
        await _cleanup(email)


@pytest.mark.asyncio
async def test_a_synopsis_asset_still_routes_to_the_synopsis_graph(monkeypatch):
    asset_id, _, email = await _make_report_asset(kind="study_summary", extra_metadata={})
    try:
        build_plan_graph = _FakeGraph([{"title": "should-not-run", "content": "", "covered": True, "citations": []}])
        synopsis_graph = _FakeGraph([{"title": "s", "content": "c", "covered": True, "citations": []}])
        monkeypatch.setattr("app.workers.tasks.get_build_plan_graph", lambda: build_plan_graph)
        monkeypatch.setattr("app.workers.tasks.get_report_graph", lambda: synopsis_graph)

        result = await _generate_report(asset_id)

        assert result == {"status": "ok", "asset_id": str(asset_id)}
        assert len(synopsis_graph.calls) == 1
        assert build_plan_graph.calls == []

        call = synopsis_graph.calls[0]
        inputs = call["inputs"]
        assert inputs["kind"] == "study_summary"
        assert "asset_ids" not in inputs

        async with async_session_factory() as session:
            asset = await AssetRepository(session).get_by_id(asset_id)
            assert asset.processing_status == AssetProcessingStatus.COMPLETED
            assert asset.asset_metadata["sections"] == synopsis_graph.sections
    finally:
        await _cleanup(email)
