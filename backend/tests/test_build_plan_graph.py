"""End-to-end test of the compiled build-plan graph (Milestone 10 step
5). `tests/test_build_plan_nodes.py` tests every node in isolation and
`tests/test_build_plan_dispatch.py` tests dispatch against a fake
graph -- nothing exercises `get_build_plan_graph().ainvoke(...)` itself,
which is what actually proves the `operator.add` reducer on the
`sections` channel yields all five sections, in
`BUILD_PLAN_SECTION_TITLES` order, exactly as `render_docx`/`render_pdf`
require.

Mirrors `tests/test_reports_graph.py`'s structure and fakes.
"""

import json

import pytest

from app.agents.reports.build_plan_graph import get_build_plan_graph
from app.agents.reports.build_plan_nodes import BuildPlanDependencies
from app.agents.reports.build_plan_state import BUILD_PLAN_SECTION_TITLES, UNCOVERED
from app.agents.reports.state import ProcessedDocument, SectionEvidence


class FakeGateway:
    """Reuses `tests/test_build_plan_nodes.py`'s `FakeGateway` shape:
    takes `*contents` and pops through them one call at a time, so each
    node in the graph gets a different canned response."""

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

    async def list_selected(self, project_id, asset_ids):
        return self._documents


class FakeSectionSearcher:
    def __init__(self, *, asset_ids: tuple[str, ...] = ("asset-1",)) -> None:
        self._asset_ids = asset_ids

    async def search(self, *, owner_id, project_id, query, limit):
        return [
            SectionEvidence(
                asset_id=asset_id,
                title="A Paper",
                file_name="paper.pdf",
                snippet=f"Evidence about {query} from {asset_id}.",
            )
            for asset_id in self._asset_ids
        ]


class FakePaperProvider:
    async def search(self, *, query, limit):
        return [
            {
                "openalex_id": "https://openalex.org/W1",
                "title": "A Better Classifier",
                "authors": ["A. Author"],
                "year": 2025,
                "cited_by_count": 12,
                "landing_url": "https://openalex.example/W1",
                "oa_pdf_url": None,
                "relevance_note": "Abstract: improves on ResNet-50.",
            }
        ]


class FakeWebProvider:
    live = True

    async def search(self, *, query, limit):
        return [
            {
                "source": "web",
                "provider": "tavily_web_v1",
                "reference": "https://github.example/repo",
                "title": "An open-source implementation",
                "snippet": "A PyTorch reimplementation with pretrained weights.",
                "score": 0.9,
                "simulated": False,
                "rank": 1,
            }
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

TOOLS_JSON = json.dumps(
    {
        "tools": [
            {
                "stage": "modelling",
                "name": "PyTorch",
                "reason": "A solid default for training.",
                "alternatives": ["JAX"],
            }
        ]
    }
)

PROCESS_JSON = json.dumps(
    {
        "phases": [
            {
                "name": "Baseline",
                "steps": ["Fine-tune ResNet-50 on the dataset."],
                "definition_of_done": "A user can upload an image and get a prediction.",
                "risks": [],
            }
        ]
    }
)


async def _run_graph(*, gateway, searcher, paper_provider, web_provider):
    dependencies = BuildPlanDependencies(
        document_lister=FakeDocumentLister([_document()]),
        section_searcher=searcher,
        llm_gateway=gateway,
        paper_provider=paper_provider,
        web_provider=web_provider,
    )
    graph = get_build_plan_graph()
    return await graph.ainvoke(
        {
            "report_id": "r1",
            "project_id": "p1",
            "owner_id": "u1",
            "asset_ids": ["asset-1"],
        },
        config={"configurable": {"dependencies": dependencies}},
    )


@pytest.mark.asyncio
async def test_the_graph_produces_all_five_sections_in_order():
    gateway = FakeGateway(FINDINGS_JSON, TOOLS_JSON, PROCESS_JSON)
    result = await _run_graph(
        gateway=gateway,
        searcher=FakeSectionSearcher(),
        paper_provider=FakePaperProvider(),
        web_provider=FakeWebProvider(),
    )

    titles = [section["title"] for section in result["sections"]]
    assert titles == BUILD_PLAN_SECTION_TITLES
    # No node re-emitted another's section under the `operator.add` reducer.
    assert len(titles) == len(set(titles))


@pytest.mark.asyncio
async def test_the_graph_completes_and_keeps_section_order_with_no_research_providers():
    gateway = FakeGateway(FINDINGS_JSON, TOOLS_JSON, PROCESS_JSON)
    result = await _run_graph(
        gateway=gateway,
        searcher=FakeSectionSearcher(),
        paper_provider=None,
        web_provider=None,
    )

    titles = [section["title"] for section in result["sections"]]
    assert titles == BUILD_PLAN_SECTION_TITLES
    assert len(titles) == len(set(titles))

    follow_up = result["sections"][1]
    implementations = result["sections"][2]
    assert follow_up["title"] == "Follow-Up Research"
    assert implementations["title"] == "Implementations and Resources"
    assert follow_up["content"] == UNCOVERED
    assert implementations["content"] == UNCOVERED
    assert follow_up["covered"] is False
    assert implementations["covered"] is False
