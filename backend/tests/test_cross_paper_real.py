"""Deterministic provider-boundary regressions; not live-model quality evidence."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.planner.cross_paper import synthesize_cross_paper
from app.agents.planner.cross_paper_evidence import source_version, understanding_cache_key
from app.core.llm.gateway import LLMResponse
from app.modules.research.schemas import (
    AnalyzeDocumentRequest, CrossPaperAnalysisRequest, DocumentPassage,
    ResearchDocumentUnderstanding, ResearchGoal, SufficiencyStatus,
)
from app.modules.research.service import ResearchAssetNotFoundError, ResearchService

GOAL = ResearchGoal(type="understand", description="Compare the reported effect.")


def paper(title, content):
    asset_id = uuid.uuid4()
    return ResearchDocumentUnderstanding(
        asset_id=asset_id, title=title, results=content,
        source_passages=[DocumentPassage(
            asset_id=asset_id, chunk_id=uuid.uuid4(), chunk_index=3, page=2, content=content,
        )],
        sufficiency=SufficiencyStatus.SUFFICIENT, sufficiency_reason="Results are stated.",
    )


@pytest.fixture
def papers():
    return [paper("Trial A", "Treatment improved survival by 10%."),
            paper("Trial B", "Treatment reduced survival by 10%.")]


def comparison_payload(papers):
    refs = [{"paper_id": str(p.asset_id), "paper_title": "Wrong model title",
             "chunk_id": str(p.source_passages[0].chunk_id), "quote": p.results,
             "page": 999, "chunk_index": 999} for p in papers]
    return {
        "primary_paper_id": str(uuid.uuid4()), "supporting_paper_ids": [str(uuid.uuid4())],
        "comparison_items": [{
            "topic": "Survival", "primary_claim": papers[0].results,
            "supporting_claims": [papers[1].results], "relationship": "CONTRADICTS",
            "evidence": "The reported effects differ in direction.", "certainty": "explicit",
            "source_references": refs,
        }], "gap_resolutions": [], "hypotheses": [],
    }


def mock_gateway(monkeypatch, payload):
    gateway = AsyncMock()
    gateway.generate.return_value = LLMResponse(
        content=json.dumps(payload), model="test-model-v1", provider="test", latency_ms=0,
    )
    monkeypatch.setattr("app.agents.planner.cross_paper.get_llm_gateway", lambda: gateway)
    return gateway


@pytest.mark.asyncio
async def test_conflicting_results_preserve_real_ids_quotes_and_locations(papers, monkeypatch):
    gateway = mock_gateway(monkeypatch, comparison_payload(papers))
    result = await synthesize_cross_paper(papers[0], papers[1:], GOAL)
    assert result.primary_paper_id == papers[0].asset_id
    assert result.supporting_paper_ids == [papers[1].asset_id]
    assert result.comparison_items[0].relationship == "CONTRADICTS"
    for reference, source in zip(result.comparison_items[0].source_references, papers):
        assert reference.paper_title == source.title
        assert reference.chunk_id == source.source_passages[0].chunk_id
        assert reference.quote == source.results
        assert reference.page == 2 and reference.chunk_index == 3
    assert str(papers[0].asset_id) in gateway.generate.call_args.kwargs["prompt"]


@pytest.mark.parametrize("corruption,reason", [
    ("missing", "missing source references"),
    ("foreign_paper", "Invalid source reference"),
    ("foreign_chunk", "does not belong"),
    ("invented_quote", "not present"),
    ("cross_source_claim", "Primary claim"),
    ("invented_support", "Supporting claim"),
    ("guaranteed_hypothesis", "deterministic claim"),
    ("gap_primary_transfer", "missing primary value"),
])
@pytest.mark.asyncio
async def test_rejects_ungrounded_outputs(papers, monkeypatch, corruption, reason):
    payload = comparison_payload(papers)
    item = payload["comparison_items"][0]
    refs = item["source_references"]
    if corruption == "missing":
        item["source_references"] = []
    elif corruption == "foreign_paper":
        refs[0]["paper_id"] = str(uuid.uuid4())
    elif corruption == "foreign_chunk":
        refs[0]["chunk_id"] = refs[1]["chunk_id"]
    elif corruption == "invented_quote":
        refs[0]["quote"] = "Survival improved by 90%."
    elif corruption == "cross_source_claim":
        item["primary_claim"] = papers[1].results
    elif corruption == "invented_support":
        item["supporting_claims"] = ["An invented method."]
    elif corruption == "guaranteed_hypothesis":
        payload["hypotheses"] = [{
            "description": "This will improve survival.", "supporting_sources": refs,
            "rationale": "Test", "certainty": "interpreted",
        }]
    else:
        payload["gap_resolutions"] = [{
            "gap_description": "Primary dose missing", "supporting_paper_ids": [str(papers[1].asset_id)],
            "resolution_state": "RESOLVED", "explanation": "Copied supporting dose", "evidence": "Dose",
            "source_references": [refs[1]],
        }]
    mock_gateway(monkeypatch, payload)
    with pytest.raises(ValueError, match=reason):
        await synthesize_cross_paper(papers[0], papers[1:], GOAL)


@pytest.mark.asyncio
async def test_missing_passages_return_insufficiency_without_a_provider_call(papers, monkeypatch):
    gateway = mock_gateway(monkeypatch, {})
    papers[1].source_passages = []
    result = await synthesize_cross_paper(papers[0], papers[1:], GOAL)
    assert result.status == "insufficient_evidence"
    assert result.comparison_items == []
    gateway.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrelated_primary_quote_cannot_resolve_supporting_only_parameter(papers, monkeypatch):
    from app.modules.research.schemas import ResearchGap

    papers[0].missing_information = [ResearchGap(
        gap_type="parameter", classification="required", description="Primary dose missing",
        why_needed="Needed to reproduce the experiment",
    )]
    papers[1].source_passages[0].content += " The supporting dose was 10 mg."
    payload = comparison_payload(papers)
    payload["gap_resolutions"] = [{
        "gap_id": "0", "gap_description": "Primary dose missing",
        "supporting_paper_ids": [str(papers[1].asset_id)], "resolution_state": "RESOLVED",
        "explanation": "Use the supporting paper's dose", "evidence": "10 mg",
        "source_references": [payload["comparison_items"][0]["source_references"][0], {
            "paper_id": str(papers[1].asset_id), "paper_title": papers[1].title,
            "chunk_id": str(papers[1].source_passages[0].chunk_id), "quote": "The supporting dose was 10 mg.",
        }],
    }]
    mock_gateway(monkeypatch, payload)
    with pytest.raises(ValueError, match="still declares missing information"):
        await synthesize_cross_paper(papers[0], papers[1:], GOAL)


@pytest.mark.parametrize("foreign", ["project", "owner", "missing"])
@pytest.mark.asyncio
async def test_all_assets_authorized_before_any_extraction(monkeypatch, foreign):
    owner_id, project_id, primary_id, supporting_id = (uuid.uuid4() for _ in range(4))
    primary = SimpleNamespace(id=primary_id, owner_id=owner_id, project_id=project_id)
    supporting = SimpleNamespace(id=supporting_id, owner_id=owner_id, project_id=project_id)
    if foreign == "project":
        supporting.project_id = uuid.uuid4()
    if foreign == "owner":
        supporting.owner_id = uuid.uuid4()
    session = AsyncMock()
    session.get.side_effect = [primary, None if foreign == "missing" else supporting]
    service = ResearchService(session)
    service._projects.get_by_id = AsyncMock(return_value=SimpleNamespace(owner_id=owner_id))
    load = AsyncMock()
    monkeypatch.setattr("app.agents.planner.cross_paper_evidence.load_source", load)
    with pytest.raises(ResearchAssetNotFoundError):
        await service.synthesize_cross_paper(owner_id, primary_id, project_id,
            CrossPaperAnalysisRequest(supporting_asset_ids=[supporting_id], goal=GOAL))
    load.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("changed", ["checksum", "asset_version", "chunk_fingerprint", "extractor_version", "model_identity", "prompt_version", "goal", "context"])
def test_understanding_cache_key_invalidates_every_relevant_input(changed):
    asset = SimpleNamespace(id=uuid.uuid4(), title="Trial", checksum="a" * 64, version=1)
    chunk = SimpleNamespace(id=uuid.uuid4(), chunk_index=0, page_number=1, content="Evidence")
    version = source_version(asset, [chunk])
    request = AnalyzeDocumentRequest(goal=GOAL)
    before = understanding_cache_key(asset, version, request)
    if changed == "goal":
        request.goal = ResearchGoal(type="reproduce", description="Reproduce")
    elif changed == "context":
        from app.modules.research.schemas import ResearchWorkspaceContext
        request.workspace_context = ResearchWorkspaceContext(active_entity="new context")
    elif changed == "asset_version":
        version.asset_version += 1
    else:
        setattr(version, changed, "changed")
    assert before != understanding_cache_key(asset, version, request)
