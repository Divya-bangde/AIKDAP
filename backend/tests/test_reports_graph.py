"""The report-generation LangGraph graph (Milestone 10 step 4 -- spec
section 5: section lists per kind, uncovered sections, and
references built only from the project's documents).

Uses fakes for the LLM gateway and the knowledge-base search -- no live
model call and no real embeddings, matching `FakeGateway` in
`tests/test_paper_suggestion.py`.
"""

import json

import pytest

from app.agents.reports.graph import get_report_graph
from app.agents.reports.nodes import ReportGraphDependencies
from app.agents.reports.state import SECTION_TITLES


class FakeGateway:
    def __init__(self, content: str | None = None, *, raises: Exception | None = None) -> None:
        self._content = content
        self._raises = raises
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return LLMResponse(content=self._content, model="fake-model", provider="fake", latency_ms=5)


class FakeDocumentLister:
    def __init__(self, documents: list[dict]) -> None:
        self._documents = documents

    async def list_processed(self, project_id):
        return self._documents


class FakeSectionSearcher:
    """Returns `evidence_by_title[title]` (default `[]`) for every
    section query, regardless of the actual query text -- the graph
    tests care about section-level behavior, not retrieval ranking."""

    def __init__(self, evidence_by_title: dict[str, list[dict]]) -> None:
        self._evidence_by_title = evidence_by_title
        self.calls: list[dict] = []

    async def search(self, *, owner_id, project_id, query, limit):
        self.calls.append({"owner_id": owner_id, "project_id": project_id, "query": query, "limit": limit})
        return self._evidence_by_title.get(query, [])


_DOCS = [
    {"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "summary": "Studies poultry disease.", "topics": ["poultry"]},
]


def _section_response(content: str, cited_asset_ids: list[str]) -> str:
    return json.dumps({"content": content, "cited_asset_ids": cited_asset_ids})


async def _run_graph(*, kind: str, gateway, searcher, documents=None, tracker=None):
    from app.agents.planner.tracking import TRACKER_CONFIG_KEY

    dependencies = ReportGraphDependencies(
        document_lister=FakeDocumentLister(documents if documents is not None else _DOCS),
        section_searcher=searcher,
        llm_gateway=gateway,
    )
    configurable = {"dependencies": dependencies}
    if tracker is not None:
        configurable[TRACKER_CONFIG_KEY] = tracker
    graph = get_report_graph()
    return await graph.ainvoke(
        {"report_id": "r1", "project_id": "p1", "owner_id": "u1", "kind": kind},
        config={"configurable": configurable},
    )


@pytest.mark.asyncio
async def test_study_summary_produces_its_three_sections():
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    gateway = FakeGateway(_section_response("Section text.", ["a1"]))
    searcher = FakeSectionSearcher(evidence)

    result = await _run_graph(kind="study_summary", gateway=gateway, searcher=searcher)

    titles = [section["title"] for section in result["sections"]]
    assert titles == SECTION_TITLES["study_summary"]


@pytest.mark.asyncio
async def test_project_synopsis_produces_its_nine_sections():
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    gateway = FakeGateway(_section_response("Section text.", ["a1"]))
    searcher = FakeSectionSearcher(evidence)

    result = await _run_graph(kind="project_synopsis", gateway=gateway, searcher=searcher)

    titles = [section["title"] for section in result["sections"]]
    assert titles == SECTION_TITLES["project_synopsis"]


@pytest.mark.asyncio
async def test_a_section_with_no_evidence_is_marked_not_covered_verbatim():
    gateway = FakeGateway(_section_response("Should never be used.", ["a1"]))
    searcher = FakeSectionSearcher({})  # every query returns []

    result = await _run_graph(kind="study_summary", gateway=gateway, searcher=searcher)

    for section in result["sections"]:
        if section["title"] == "References":
            continue
        assert section["content"] == "Not covered by your documents."
        assert section["covered"] is False
    # No evidence anywhere -- the LLM must never have been called.
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_references_are_built_only_from_documents_actually_cited():
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    # The model hallucinates a second, non-existent asset id -- it must
    # never reach the stored References section.
    gateway = FakeGateway(_section_response("Section text.", ["a1", "a1-does-not-exist"]))
    searcher = FakeSectionSearcher(evidence)

    result = await _run_graph(kind="project_synopsis", gateway=gateway, searcher=searcher)

    references = next(section for section in result["sections"] if section["title"] == "References")
    assert "Poultry Disease Paper" in references["content"]
    assert "poultry.pdf" in references["content"]
    assert "a1-does-not-exist" not in references["content"]


@pytest.mark.asyncio
async def test_an_empty_model_response_clears_citations_even_if_the_model_named_ids():
    """Final-review finding M3: when evidence exists but the model
    returns empty content, the section falls back to "Not covered by
    your documents." -- its `cited_asset_ids` must be cleared too, or a
    section reported as uncovered would still leak into References."""
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    # The model returns blank content but still names a cited asset --
    # a genuinely malformed but parseable response.
    gateway = FakeGateway(_section_response("   ", ["a1"]))
    searcher = FakeSectionSearcher(evidence)

    result = await _run_graph(kind="project_synopsis", gateway=gateway, searcher=searcher)

    for section in result["sections"]:
        if section["title"] == "References":
            continue
        assert section["content"] == "Not covered by your documents."
        assert section["covered"] is False
        assert section["citations"] == []

    references = next(section for section in result["sections"] if section["title"] == "References")
    assert references["content"] == "Not covered by your documents."
    assert references["covered"] is False


@pytest.mark.asyncio
async def test_an_unparseable_llm_response_raises():
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    gateway = FakeGateway("not json")
    searcher = FakeSectionSearcher(evidence)

    with pytest.raises(Exception):
        await _run_graph(kind="study_summary", gateway=gateway, searcher=searcher)


from app.agents.planner.tracking import NodeExecutionTracker


class RecordingTracker(NodeExecutionTracker):
    def __init__(self) -> None:
        self.started: list[str] = []
        self.succeeded: dict[str, dict] = {}
        self.failed: list[str] = []

    async def on_node_start(self, node):
        self.started.append(node)

    async def on_node_success(self, node, update, duration_ms):
        self.succeeded[node] = update.get("step") or {}

    async def on_node_failure(self, node, error, duration_ms, critical):
        self.failed.append(node)


_REPORT_NODES = ["collect_documents", "retrieve_evidence", "write_sections", "coverage_check"]


@pytest.mark.asyncio
async def test_every_report_node_runs_in_order_and_reports_a_step_summary():
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    tracker = RecordingTracker()

    await _run_graph(
        kind="study_summary",
        gateway=FakeGateway(_section_response("Section text.", ["a1"])),
        searcher=FakeSectionSearcher(evidence),
        tracker=tracker,
    )

    assert tracker.started == _REPORT_NODES
    assert list(tracker.succeeded) == _REPORT_NODES
    for node in _REPORT_NODES:
        assert tracker.succeeded[node]["summary"], node
    assert tracker.succeeded["collect_documents"]["output"] == {"document_count": 1}
    assert tracker.succeeded["write_sections"]["output"]["covered"] == 3


@pytest.mark.asyncio
async def test_a_retrieval_failure_is_recorded_against_retrieve_evidence_and_stops_the_run():
    class _FailingSearcher:
        async def search(self, **kwargs):
            raise RuntimeError("search backend down")

    tracker = RecordingTracker()
    gateway = FakeGateway(_section_response("unused", []))

    with pytest.raises(RuntimeError):
        await _run_graph(kind="study_summary", gateway=gateway, searcher=_FailingSearcher(), tracker=tracker)

    assert tracker.started == ["collect_documents", "retrieve_evidence"]
    assert tracker.failed == ["retrieve_evidence"]
    assert gateway.calls == []
