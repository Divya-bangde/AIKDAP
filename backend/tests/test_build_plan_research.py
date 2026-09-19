"""`research_further` degradation against real provider code.

Drives the actual `OpenAlexProvider` and `TavilyWebResearchProvider`
through `httpx.MockTransport`, so request building, parsing, and the
node's error handling are all exercised -- no network, no API key.
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.agents.planner.nodes import TavilyWebResearchProvider
from app.agents.planner.paper_suggestion import OpenAlexProvider
from app.agents.reports.build_plan_nodes import BuildPlanDependencies, research_further_node
from app.agents.reports.build_plan_state import UNCOVERED

FINDINGS = {
    "method": ["A fine-tuned convolutional classifier."],
    "models": ["ResNet-50"],
    "datasets": ["A private poultry dataset."],
    "metrics": ["Accuracy"],
    "compute": ["One NVIDIA V100."],
    "limitations": ["The dataset is not public."],
}


class _UnusedLister:
    async def list_selected(self, project_id, asset_ids):
        return []


class _UnusedSearcher:
    async def search(self, *, owner_id, project_id, query, limit):
        return []


class _UnusedGateway:
    async def generate(self, **kwargs):
        raise AssertionError("research_further must not call the model.")


def _dependencies(paper_provider, web_provider) -> BuildPlanDependencies:
    return BuildPlanDependencies(
        document_lister=_UnusedLister(),
        section_searcher=_UnusedSearcher(),
        llm_gateway=_UnusedGateway(),
        paper_provider=paper_provider,
        web_provider=web_provider,
    )


def _openalex(handler) -> OpenAlexProvider:
    return OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5.0, transport=httpx.MockTransport(handler)
    )


def _tavily(handler) -> TavilyWebResearchProvider:
    return TavilyWebResearchProvider(
        api_key=SecretStr("test-key"), timeout=5.0, transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_both_providers_missing_a_key_leave_the_report_completable():
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": _dependencies(None, None)}}
    )

    assert update["links"] == []
    assert [section["content"] for section in update["sections"]] == [UNCOVERED, UNCOVERED]
    assert update["step"]["output"] == {
        "openalex": "skipped: no API key configured",
        "tavily": "skipped: no API key configured",
        "link_count": 0,
    }


@pytest.mark.asyncio
async def test_a_failing_openalex_is_recorded_without_leaking_the_key_or_url():
    def openalex_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server exploded"})

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://github.example/repo",
                        "title": "An implementation",
                        "content": "A PyTorch reimplementation.",
                        "score": 0.9,
                    }
                ]
            },
        )

    dependencies = _dependencies(_openalex(openalex_handler), _tavily(tavily_handler))
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": dependencies}}
    )

    payload = json.dumps(update["step"])
    assert "test-key" not in payload
    assert "api.openalex.org" not in payload
    assert update["step"]["output"]["openalex"] == "failed (RuntimeError)"
    # Tavily's results still made it into the document.
    assert update["step"]["output"]["tavily"] == "ok"
    assert "https://github.example/repo" in update["sections"][1]["content"]


@pytest.mark.asyncio
async def test_a_transport_level_openalex_failure_does_not_leak_the_request_url():
    """`OpenAlexProvider.search` scrubs `HTTPStatusError` itself, so the
    500 case above never reaches the node carrying a URL -- it would
    pass even if the node recorded `str(exc)` unscrubbed. A
    transport-level exception (raised by httpx itself, below
    `OpenAlexProvider`) is not caught there, and its message embeds the
    full request URL -- which, for OpenAlex, carries `api_key` as a
    query parameter. This is the case that actually exercises the
    node's "type name only" handling."""

    def openalex_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "Connection failed for https://api.openalex.org/works?api_key=sk-live-SECRET123"
        )

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://github.example/repo",
                        "title": "An implementation",
                        "content": "A PyTorch reimplementation.",
                        "score": 0.9,
                    }
                ]
            },
        )

    dependencies = _dependencies(_openalex(openalex_handler), _tavily(tavily_handler))
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": dependencies}}
    )

    payload = json.dumps(update["step"])
    assert "sk-live-SECRET123" not in payload
    assert "api.openalex.org" not in payload
    assert update["step"]["output"]["openalex"] == "failed (ConnectError)"
    assert update["step"]["output"]["tavily"] == "ok"


@pytest.mark.asyncio
async def test_a_failing_tavily_is_recorded_while_openalex_results_survive():
    def openalex_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "A Better Classifier",
                        "publication_year": 2025,
                        "cited_by_count": 12,
                        "authorships": [{"author": {"display_name": "A. Author"}}],
                        "primary_location": {"landing_page_url": "https://openalex.example/W1"},
                        "best_oa_location": {},
                        "abstract_inverted_index": {"Improves": [0], "accuracy": [1]},
                    }
                ]
            },
        )

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    dependencies = _dependencies(_openalex(openalex_handler), _tavily(tavily_handler))
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": dependencies}}
    )

    assert update["step"]["output"]["openalex"] == "ok"
    assert update["step"]["output"]["tavily"] == "failed (ConnectTimeout)"
    assert "https://openalex.example/W1" in update["sections"][0]["content"]
    assert update["sections"][1]["content"] == UNCOVERED
