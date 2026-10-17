"""Real disposable PostgreSQL tests, with deterministic LLM boundary responses."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
import httpx
from sqlalchemy import func, select

from app.core.llm.gateway import LLMResponse
from app.database.session import open_session
from app.modules.assets.enums import AssetType
from app.modules.assets.models import Asset
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import CrossPaperArtifact, DocumentUnderstandingCache, ResearchRun
from app.modules.research.schemas import (
    CrossPaperAnalysisRequest, ResearchDocumentUnderstanding, ResearchGoal,
)
from app.modules.research.service import ResearchAssetNotFoundError, ResearchService

pytestmark = pytest.mark.asyncio
GOAL = ResearchGoal(type="understand", description="Compare the survival result")


async def make_paper(session, project, *, title, content=None):
    asset = Asset(
        project_id=project.id, owner_id=project.owner_id, title=title,
        asset_type=AssetType.DOCUMENT, mime_type="text/plain", file_name="paper.txt",
        file_extension="txt", file_size=100, storage_path="test/paper.txt", checksum="a" * 64,
        version=1, tags=[], asset_metadata={"untouched": True}, ai_profile={},
    )
    session.add(asset)
    await session.flush()
    if content is not None:
        session.add(KnowledgeChunk(
            asset_id=asset.id, project_id=project.id, chunk_index=0, page_number=4, content=content,
        ))
    await session.commit()
    return asset


@pytest.fixture
def deterministic_models(monkeypatch):
    async def extract(asset_id, project_id, request, session, **kwargs):
        return ResearchDocumentUnderstanding(
            title="Model supplied title", results=kwargs["source_chunks"][0].content,
            sufficiency="sufficient", sufficiency_reason="The results are stated.",
        )
    extractor = AsyncMock(side_effect=extract)
    monkeypatch.setattr("app.agents.planner.cross_paper_evidence.analyze_research_document", extractor)
    async def reduce(**kwargs):
        payload = json.loads(kwargs["prompt"])
        primary, supporting = payload["primary"], payload["supporting"][0]
        refs = [{"paper_id": p["asset_id"], "paper_title": p["title"],
                 "chunk_id": p["source_passages"][0]["chunk_id"], "quote": p["results"]}
                for p in (primary, supporting)]
        response = {
            "primary_paper_id": primary["asset_id"], "supporting_paper_ids": [supporting["asset_id"]],
            "comparison_items": [{"topic": "Survival", "primary_claim": primary["results"],
                "supporting_claims": [supporting["results"]], "relationship": "CONTRADICTS",
                "evidence": "Opposite effects", "certainty": "explicit", "source_references": refs}],
            "gap_resolutions": [], "hypotheses": [],
        }
        return LLMResponse(content=json.dumps(response), model="deterministic-test", provider="test", latency_ms=0)
    gateway = AsyncMock()
    gateway.generate.side_effect = reduce
    monkeypatch.setattr("app.agents.planner.cross_paper.get_llm_gateway", lambda: gateway)
    return extractor, gateway


async def test_conflicting_comparison_survives_new_session_and_cache_invalidates(session, project, deterministic_models):
    primary = await make_paper(session, project, title="Trial A", content="Treatment improved survival by 10%.")
    supporting = await make_paper(session, project, title="Trial B", content="Treatment reduced survival by 10%.")
    request = CrossPaperAnalysisRequest(supporting_asset_ids=[supporting.id], goal=GOAL)
    service = ResearchService(session)
    saved = await service.synthesize_cross_paper(project.owner_id, primary.id, project.id, request)
    assert saved.artifact_id is not None
    assert saved.status == "completed"
    assert deterministic_models[0].await_count == 2
    assert await session.scalar(select(func.count()).select_from(DocumentUnderstandingCache).where(
        DocumentUnderstandingCache.asset_id.in_([primary.id, supporting.id]))) == 2

    async with open_session() as restored_session:
        restored_service = ResearchService(restored_session)
        restored = await restored_service.restore_comparison(project.owner_id, project.id, asset_id=primary.id)
        assert restored.model_dump() == saved.model_dump()
        by_id = await restored_service.restore_comparison(project.owner_id, project.id, artifact_id=saved.artifact_id)
        assert by_id.artifact_id == saved.artifact_id
        for ref in restored.comparison_items[0].source_references:
            chunk = await restored_session.get(KnowledgeChunk, ref.chunk_id)
            assert chunk.asset_id == ref.paper_id
            assert ref.quote in chunk.content
            assert ref.page == chunk.page_number
        await restored_service.synthesize_cross_paper(project.owner_id, primary.id, project.id, request)
        assert deterministic_models[0].await_count == 2  # reusable, keyed cache hit

    supporting.checksum = "b" * 64
    supporting.version = 2
    await session.commit()
    stale = await service.restore_comparison(project.owner_id, project.id, artifact_id=saved.artifact_id)
    assert stale.stale is True
    await service.synthesize_cross_paper(project.owner_id, primary.id, project.id, request)
    assert deterministic_models[0].await_count == 3
    changed_goal = request.model_copy(update={"goal": ResearchGoal(type="reproduce", description="Reproduce it")})
    await service.synthesize_cross_paper(project.owner_id, primary.id, project.id, changed_goal)
    assert deterministic_models[0].await_count == 5


async def test_insufficiency_is_persisted_and_restored(session, project, deterministic_models):
    primary = await make_paper(session, project, title="Unprocessed A")
    supporting = await make_paper(session, project, title="Unprocessed B")
    service = ResearchService(session)
    result = await service.synthesize_cross_paper(project.owner_id, primary.id, project.id,
        CrossPaperAnalysisRequest(supporting_asset_ids=[supporting.id], goal=GOAL))
    assert result.status == "insufficient_evidence"
    assert result.comparison_items == []
    deterministic_models[0].assert_not_awaited()
    deterministic_models[1].generate.assert_not_awaited()
    async with open_session() as db:
        restored = await ResearchService(db).restore_comparison(project.owner_id, project.id, asset_id=primary.id)
        assert restored.artifact_id == result.artifact_id
        assert restored.status == "insufficient_evidence"


async def test_restore_reauthorizes_supporting_asset_and_hides_foreign_artifact(session, project, deterministic_models):
    primary = await make_paper(session, project, title="A")
    supporting = await make_paper(session, project, title="B")
    service = ResearchService(session)
    result = await service.synthesize_cross_paper(project.owner_id, primary.id, project.id,
        CrossPaperAnalysisRequest(supporting_asset_ids=[supporting.id], goal=GOAL))
    assert await service.restore_comparison(project.owner_id, project.id, artifact_id=uuid.uuid4()) is None
    await session.delete(supporting)
    await session.commit()
    assert await session.get(CrossPaperArtifact, result.artifact_id) is not None
    with pytest.raises(ResearchAssetNotFoundError):
        await service.restore_comparison(project.owner_id, project.id, artifact_id=result.artifact_id)


async def test_research_pagination_over_100_with_tied_timestamps_is_compact_and_stable(session, project):
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    runs = [ResearchRun(
        owner_id=project.owner_id, project_id=project.id, query=f"Question {i}",
        status=ResearchRunStatus.COMPLETED, include_assets=True, include_web=False,
        max_results=5, final_answer="A large answer should never be in list summaries",
        created_at=timestamp,
    ) for i in range(107)]
    session.add_all(runs)
    await session.commit()
    expected = sorted([r.id for r in runs], reverse=True)
    cursor, seen = None, []
    service = ResearchService(session)
    while True:
        page = await service.page_runs(project.owner_id, project_id=project.id, cursor=cursor, limit=20)
        assert all("final_answer" not in row.model_dump() for row in page.items)
        seen.extend(row.id for row in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == expected
    first = await service.page_runs(project.owner_id, project_id=project.id, limit=20)
    with pytest.raises(HTTPException, match="Invalid pagination cursor"):
        await service.page_runs(uuid.uuid4(), cursor=first.next_cursor, limit=20)
    assert (await service.page_runs(uuid.uuid4(), limit=20)).items == []


async def test_http_comparison_restore_not_found_and_page_contract(session, project, deterministic_models):
    from app.modules.auth.security import get_current_user
    from app.modules.research.dependencies import get_research_service
    from app.modules.research.router import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=project.owner_id)
    app.dependency_overrides[get_research_service] = lambda: ResearchService(session)
    primary = await make_paper(session, project, title="A")
    supporting = await make_paper(session, project, title="B")
    url = f"/research/documents/{primary.id}/cross-paper-analysis?project_id={project.id}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.get(url)
        assert empty.status_code == 200 and empty.json() is None
        created = await client.post(url, json={"supporting_asset_ids": [str(supporting.id)], "goal": GOAL.model_dump()})
        assert created.status_code == 200
        assert created.json()["artifact_id"]
        assert (await client.get(url)).json() == created.json()
        artifact = await client.get(f"/research/comparisons/{created.json()['artifact_id']}?project_id={project.id}")
        assert artifact.status_code == 200 and artifact.json() == created.json()
        invalid = await client.post(url, json={"supporting_asset_ids": [str(uuid.uuid4())], "goal": GOAL.model_dump()})
        assert invalid.status_code == 404
        page = await client.get(f"/research/runs/page?project_id={project.id}&limit=10")
        assert page.status_code == 200 and page.json() == {"items": [], "next_cursor": None}
