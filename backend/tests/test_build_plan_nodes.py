"""Unit tests for the build-plan graph's nodes (Milestone 10 step 5).

Every node runs against fakes -- a fake LLM gateway (the `FakeGateway`
shape `tests/test_paper_suggestion.py` established), fake providers, and
a fake section searcher. No live model call, no network, no database.
"""

import json

import pytest

from app.agents.reports.build_plan_graph import get_build_plan_graph
from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.agents.reports.build_plan_state import BUILD_PLAN_SECTION_TITLES, BuildPlanNode


def test_registry_declares_the_four_nodes_with_research_non_critical():
    assert list(BUILD_PLAN_AGENT_REGISTRY) == [
        BuildPlanNode.EXTRACT.value,
        BuildPlanNode.RESEARCH_FURTHER.value,
        BuildPlanNode.RECOMMEND_TOOLS.value,
        BuildPlanNode.RECOMMEND_PROCESS.value,
    ]
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.EXTRACT.value].critical is True
    # Spec section 6/7: a missing or failing provider must not fail the report.
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RESEARCH_FURTHER.value].critical is False
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RECOMMEND_TOOLS.value].critical is True
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RECOMMEND_PROCESS.value].critical is True


def test_graph_compiles_with_every_registered_node():
    graph = get_build_plan_graph()
    assert set(BUILD_PLAN_AGENT_REGISTRY) <= set(graph.get_graph().nodes)


def test_section_titles_are_the_five_document_sections():
    assert BUILD_PLAN_SECTION_TITLES == [
        "What the Paper Builds",
        "Follow-Up Research",
        "Implementations and Resources",
        "Recommended Tools",
        "Build Process",
    ]


from app.agents.reports.build_plan_nodes import BuildPlanDependencies, extract_node
from app.agents.reports.build_plan_state import UNCOVERED
from app.agents.reports.state import ProcessedDocument, SectionEvidence


class FakeGateway:
    """The `FakeGateway` shape `tests/test_paper_suggestion.py` uses:
    returns canned content and records every call's kwargs."""

    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        content = self._contents.pop(0) if len(self._contents) > 1 else self._contents[0]
        return LLMResponse(content=content, model="fake-model", provider="fake", latency_ms=5)


class FakeDocumentLister:
    def __init__(self, documents: list[ProcessedDocument]) -> None:
        self._documents = documents
        self.requested_ids: list[str] | None = None

    async def list_selected(self, project_id, asset_ids):
        self.requested_ids = list(asset_ids)
        return self._documents


class FakeSectionSearcher:
    """Returns one excerpt per query unless constructed empty."""

    def __init__(self, *, empty: bool = False) -> None:
        self._empty = empty
        self.queries: list[str] = []

    async def search(self, *, owner_id, project_id, query, limit):
        self.queries.append(query)
        if self._empty:
            return []
        return [
            SectionEvidence(
                asset_id="asset-1",
                title="A Paper",
                file_name="paper.pdf",
                snippet=f"Evidence about {query}.",
            )
        ]


def _document() -> ProcessedDocument:
    return ProcessedDocument(
        asset_id="asset-1",
        title="A Paper",
        file_name="paper.pdf",
        summary="Detects disease in poultry from images.",
        topics=["poultry", "vision"],
    )


FINDINGS_JSON = json.dumps(
    {
        "method": ["A fine-tuned convolutional classifier."],
        "models": ["ResNet-50"],
        "datasets": ["A private 4,000-image poultry dataset."],
        "metrics": ["Accuracy", "F1"],
        "compute": ["One NVIDIA V100 for 6 hours."],
        "limitations": ["The dataset is not public."],
    }
)


def _dependencies(gateway, lister, searcher, *, paper_provider=None, web_provider=None):
    return BuildPlanDependencies(
        document_lister=lister,
        section_searcher=searcher,
        llm_gateway=gateway,
        paper_provider=paper_provider,
        web_provider=web_provider,
    )


def _config(dependencies):
    return {"configurable": {"dependencies": dependencies}}


@pytest.mark.asyncio
async def test_extract_node_produces_its_section_from_one_structured_llm_call():
    gateway = FakeGateway(FINDINGS_JSON)
    lister = FakeDocumentLister([_document()])
    searcher = FakeSectionSearcher()
    dependencies = _dependencies(gateway, lister, searcher)

    state = {
        "report_id": "r1",
        "project_id": "p1",
        "owner_id": "u1",
        "asset_ids": ["asset-1"],
    }
    update = await extract_node(state, _config(dependencies))

    # Only the papers the builder picked are listed.
    assert lister.requested_ids == ["asset-1"]
    # Retrieval comes from the knowledge base, one query per field.
    assert len(searcher.queries) == 6
    # Exactly one structured LLM call produces every field.
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["response_format"]["type"] == "json_schema"

    assert update["findings"]["models"] == ["ResNet-50"]
    section = update["sections"][0]
    assert section["title"] == "What the Paper Builds"
    assert section["covered"] is True
    assert "ResNet-50" in section["content"]
    assert "The dataset is not public." in section["content"]
    assert section["citations"] == ["asset-1"]


@pytest.mark.asyncio
async def test_extract_node_marks_the_section_uncovered_without_calling_the_model():
    gateway = FakeGateway(FINDINGS_JSON)
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher(empty=True))

    update = await extract_node(
        {"report_id": "r1", "project_id": "p1", "owner_id": "u1", "asset_ids": ["asset-1"]},
        _config(dependencies),
    )

    assert gateway.calls == []
    section = update["sections"][0]
    assert section["content"] == UNCOVERED
    assert section["covered"] is False
    assert section["citations"] == []
