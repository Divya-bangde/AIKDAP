"""Query decomposition for comparison questions (multi-paper retrieval)."""

import uuid
from types import SimpleNamespace

import pytest

from app.agents.planner.decomposition import MAX_SUB_QUERIES, decompose_query, parse_sub_queries
from app.agents.planner.nodes import (
    AssetRetriever,
    ExtractiveSynthesizer,
    GraphDependencies,
    asset_retrieval_node,
)
from app.agents.planner.planner import RuleBasedPlanner, get_planner
from app.core.config import settings


class _FakeGateway:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.calls = 0

    async def generate(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._content, latency_ms=5)


class _PaperRetriever(AssetRetriever):
    """Returns 3 chunks from whichever paper the query names."""

    name = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def retrieve(self, *, owner_id, project_id, query, limit, asset_id=None):
        self.queries.append(query)
        paper = "gpt3" if "GPT-3" in query else "rag"
        return [
            {"chunk_id": f"{paper}-{i}", "reference": f"{paper}#{i}", "title": paper}
            for i in range(limit)
        ]


def test_parse_extracts_tags_dedupes_and_caps() -> None:
    text = (
        "<search>RAG retriever generator</search>\n<search> GPT-3  few-shot </search>"
        "<SEARCH>rag retriever generator</SEARCH>"
        + "".join(f"<search>q{i}</search>" for i in range(9))
    )
    assert parse_sub_queries(text)[:2] == ["RAG retriever generator", "GPT-3 few-shot"]
    assert len(parse_sub_queries(text)) == MAX_SUB_QUERIES


def test_parse_keeps_tags_closed_before_a_cut_off() -> None:
    assert parse_sub_queries("<search>BERT data</search><search>GPT-3 da") == ["BERT data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gateway",
    [
        _FakeGateway(content="Sorry, I cannot."),
        _FakeGateway(content="<search>only one</search>"),
        _FakeGateway(error=RuntimeError("ollama down")),
    ],
)
async def test_decompose_falls_back_to_the_original_question(gateway) -> None:
    sub_queries, _ = await decompose_query("RAG vs GPT-3?", gateway)
    assert sub_queries == ["RAG vs GPT-3?"]


def test_planner_classifies_different_as_comparison() -> None:
    intent, _ = RuleBasedPlanner._classify("How is RAG different from GPT-3?")
    assert intent == "comparison"


async def _run_node(intent: str, gateway: _FakeGateway, retriever: _PaperRetriever) -> dict:
    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=retriever,
        web_provider=None,
        synthesizer=ExtractiveSynthesizer(),
        llm_gateway=gateway,
    )
    return await asset_retrieval_node(
        {
            "run_id": str(uuid.uuid4()),
            "owner_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "query": "How does RAG differ from GPT-3 few-shot prompting?",
            "max_results": 5,
            "plan": {"intent": intent},
        },
        {"configurable": {"dependencies": dependencies}},
    )


@pytest.mark.asyncio
async def test_comparison_retrieves_evidence_for_every_subject(monkeypatch) -> None:
    monkeypatch.setattr(settings, "query_reformulation_enabled", False)
    gateway = _FakeGateway(content="<search>RAG retriever</search><search>GPT-3 few-shot</search>")
    retriever = _PaperRetriever()

    result = await _run_node("comparison", gateway, retriever)

    papers = [doc["title"] for doc in result["retrieved_documents"]]
    assert retriever.queries == ["RAG retriever", "GPT-3 few-shot"]
    assert papers == ["rag", "gpt3"] * 3
    assert [doc["rank"] for doc in result["retrieved_documents"]] == list(range(1, 7))


@pytest.mark.asyncio
async def test_non_comparison_keeps_a_single_search(monkeypatch) -> None:
    monkeypatch.setattr(settings, "query_reformulation_enabled", False)
    gateway = _FakeGateway(content="<search>a</search><search>b</search>")
    retriever = _PaperRetriever()

    result = await _run_node("open_research", gateway, retriever)

    assert gateway.calls == 0
    assert len(retriever.queries) == 1
    assert len(result["retrieved_documents"]) == 5
