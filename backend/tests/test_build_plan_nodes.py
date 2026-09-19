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
    """Returns one excerpt per query, tagged with each given asset id,
    unless constructed empty."""

    def __init__(self, *, empty: bool = False, asset_ids: tuple[str, ...] = ("asset-1",)) -> None:
        self._empty = empty
        self._asset_ids = asset_ids
        self.queries: list[str] = []

    async def search(self, *, owner_id, project_id, query, limit):
        self.queries.append(query)
        if self._empty:
            return []
        return [
            SectionEvidence(
                asset_id=asset_id,
                title="A Paper",
                file_name="paper.pdf",
                snippet=f"Evidence about {query} from {asset_id}.",
            )
            for asset_id in self._asset_ids
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


@pytest.mark.asyncio
async def test_extract_node_ignores_evidence_from_papers_the_builder_did_not_select():
    # Every excerpt returned by the searcher is tagged with an asset id
    # that is NOT among the selected documents, so the filter must drop
    # all of it -- the node then takes the no-evidence path, exactly as
    # if the searcher had returned nothing.
    gateway = FakeGateway(FINDINGS_JSON)
    dependencies = _dependencies(
        gateway,
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(asset_ids=("asset-99",)),
    )

    update = await extract_node(
        {"report_id": "r1", "project_id": "p1", "owner_id": "u1", "asset_ids": ["asset-1"]},
        _config(dependencies),
    )

    assert gateway.calls == []
    section = update["sections"][0]
    assert section["content"] == UNCOVERED
    assert section["covered"] is False
    assert section["citations"] == []


@pytest.mark.asyncio
async def test_extract_node_grounds_only_in_selected_papers_evidence():
    # A sharper pin: unselected evidence ("asset-99") is mixed in
    # alongside selected evidence ("asset-1"). If the selection filter
    # were removed, "asset-99" would leak into both the prompt and the
    # citations -- this test fails loudly in that case.
    gateway = FakeGateway(FINDINGS_JSON)
    dependencies = _dependencies(
        gateway,
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(asset_ids=("asset-1", "asset-99")),
    )

    update = await extract_node(
        {"report_id": "r1", "project_id": "p1", "owner_id": "u1", "asset_ids": ["asset-1"]},
        _config(dependencies),
    )

    assert len(gateway.calls) == 1
    assert "asset-99" not in gateway.calls[0]["prompt"]

    section = update["sections"][0]
    assert section["citations"] == ["asset-1"]


from app.agents.reports.build_plan_nodes import research_further_node


class FakePaperProvider:
    def __init__(self, papers=None, error: Exception | None = None) -> None:
        self._papers = papers or []
        self._error = error
        self.queries: list[str] = []

    async def search(self, *, query, limit):
        self.queries.append(query)
        if self._error:
            raise self._error
        return self._papers


class FakeWebProvider:
    live = True

    def __init__(self, documents=None, error: Exception | None = None) -> None:
        self._documents = documents or []
        self._error = error
        self.queries: list[str] = []

    async def search(self, *, query, limit):
        self.queries.append(query)
        if self._error:
            raise self._error
        return self._documents


def _paper(url="https://openalex.example/W1"):
    return {
        "openalex_id": "https://openalex.org/W1",
        "title": "A Better Classifier",
        "authors": ["A. Author"],
        "year": 2025,
        "cited_by_count": 12,
        "landing_url": url,
        "oa_pdf_url": None,
        "relevance_note": "Abstract: improves on ResNet-50.",
    }


def _web(url="https://github.example/repo"):
    return {
        "source": "web",
        "provider": "tavily_web_v1",
        "reference": url,
        "title": "An open-source implementation",
        "snippet": "A PyTorch reimplementation with pretrained weights.",
        "score": 0.9,
        "simulated": False,
        "rank": 1,
    }


FINDINGS = {
    "method": ["A fine-tuned convolutional classifier."],
    "models": ["ResNet-50"],
    "datasets": ["A private 4,000-image poultry dataset."],
    "metrics": ["Accuracy"],
    "compute": ["One NVIDIA V100."],
    "limitations": ["The dataset is not public."],
}


@pytest.mark.asyncio
async def test_research_further_produces_both_sections_with_provider_links():
    papers = FakePaperProvider([_paper()])
    web = FakeWebProvider([_web()])
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=papers,
        web_provider=web,
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    titles = [section["title"] for section in update["sections"]]
    assert titles == ["Follow-Up Research", "Implementations and Resources"]
    assert all(section["covered"] for section in update["sections"])
    assert "https://openalex.example/W1" in update["sections"][0]["content"]
    assert "https://github.example/repo" in update["sections"][1]["content"]
    # Every collected link is recorded in state for the later nodes.
    assert {link["url"] for link in update["links"]} == {
        "https://openalex.example/W1",
        "https://github.example/repo",
    }
    assert update["step"]["output"]["openalex"] == "ok"
    assert update["step"]["output"]["tavily"] == "ok"


@pytest.mark.asyncio
async def test_research_further_records_a_missing_key_as_skipped_and_still_completes():
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=None,
        web_provider=None,
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    assert update["links"] == []
    assert [section["content"] for section in update["sections"]] == [UNCOVERED, UNCOVERED]
    assert update["step"]["output"]["openalex"] == "skipped: no API key configured"
    assert update["step"]["output"]["tavily"] == "skipped: no API key configured"


@pytest.mark.asyncio
async def test_research_further_records_a_raising_provider_with_a_scrubbed_error():
    secret = "sk-live-do-not-leak"
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=FakePaperProvider(error=RuntimeError(f"boom {secret}")),
        web_provider=FakeWebProvider([_web()]),
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    # The failing provider's own message never reaches the step payload.
    assert update["step"]["output"]["openalex"] == "failed (RuntimeError)"
    assert secret not in json.dumps(update["step"])
    # The other provider's results still land, and the report completes.
    assert update["sections"][0]["content"] == UNCOVERED
    assert update["sections"][1]["covered"] is True
    assert update["step"]["output"]["tavily"] == "ok"


@pytest.mark.asyncio
async def test_no_link_in_the_output_is_absent_from_the_provider_results():
    papers = FakePaperProvider([_paper("https://openalex.example/W1")])
    web = FakeWebProvider([_web("https://github.example/repo")])
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=papers,
        web_provider=web,
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    import re as _re

    allowed = {"https://openalex.example/W1", "https://github.example/repo"}
    rendered = " ".join(section["content"] for section in update["sections"])
    found = set(_re.findall(r"https?://\S+", rendered))
    assert found <= allowed, f"invented link(s): {found - allowed}"


from app.agents.reports.build_plan_nodes import recommend_process_node, recommend_tools_node

TOOLS_JSON = json.dumps(
    {
        "tools": [
            {
                "stage": "data",
                "name": "Label Studio",
                "reason": "The paper's 4,000-image dataset is private, so you must label your own.",
                "alternatives": ["CVAT", "Roboflow"],
            },
            {
                "stage": "modelling",
                "name": "PyTorch",
                "reason": "ResNet-50 pretrained weights ship with torchvision.",
                "alternatives": ["TensorFlow"],
            },
            {
                "stage": "not-a-stage",
                "name": "Something",
                "reason": "Invented stage.",
                "alternatives": [],
            },
        ]
    }
)

PROCESS_JSON = json.dumps(
    {
        "phases": [
            {
                "name": "Reproduce the paper's baseline",
                "steps": ["Obtain a comparable dataset.", "Fine-tune ResNet-50."],
                "definition_of_done": "Accuracy within 2 points of the paper's reported figure.",
                "risks": ["The dataset needs licence approval before use."],
            },
            {
                "name": "Ship a working product",
                "steps": ["Wrap the model in a FastAPI endpoint."],
                "definition_of_done": "A user can upload an image and get a prediction.",
                "risks": [],
            },
        ]
    }
)


@pytest.mark.asyncio
async def test_recommend_tools_groups_by_stage_with_reasons_and_alternatives():
    gateway = FakeGateway(TOOLS_JSON)
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher())

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    section = update["sections"][0]
    assert section["title"] == "Recommended Tools"
    assert section["covered"] is True
    # Grouped under the stage headings, in the spec's order.
    assert section["content"].index("Data") < section["content"].index("Modelling")
    assert "Label Studio" in section["content"]
    assert "Alternatives: CVAT, Roboflow" in section["content"]
    assert "4,000-image dataset is private" in section["content"]
    # A stage the model invented is dropped rather than rendered.
    assert "not-a-stage" not in section["content"]
    assert [tool["stage"] for tool in update["tools"]] == ["data", "modelling"]


@pytest.mark.asyncio
async def test_recommend_tools_marks_the_section_uncovered_when_the_model_returns_none():
    gateway = FakeGateway(json.dumps({"tools": []}))
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher())

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    assert update["sections"][0]["content"] == UNCOVERED
    assert update["sections"][0]["covered"] is False


@pytest.mark.asyncio
async def test_recommend_process_produces_phases_with_done_criteria_and_risks():
    gateway = FakeGateway(PROCESS_JSON)
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher())

    update = await recommend_process_node(
        {
            "report_id": "r1",
            "findings": FINDINGS,
            "tools": [
                {"stage": "modelling", "name": "PyTorch", "reason": "ResNet-50", "alternatives": []}
            ],
        },
        _config(dependencies),
    )

    section = update["sections"][0]
    assert section["title"] == "Build Process"
    assert section["covered"] is True
    assert "Phase 1: Reproduce the paper's baseline" in section["content"]
    assert "Definition of done: Accuracy within 2 points" in section["content"]
    assert "licence approval" in section["content"]
    assert len(update["phases"]) == 2


@pytest.mark.asyncio
async def test_a_model_written_url_never_reaches_a_section():
    leaky = json.dumps(
        {
            "tools": [
                {
                    "stage": "modelling",
                    "name": "PyTorch",
                    "reason": "See https://invented.example/not-real for details.",
                    "alternatives": ["JAX (www.invented.example)"],
                }
            ]
        }
    )
    dependencies = _dependencies(
        FakeGateway(leaky), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    content = update["sections"][0]["content"]
    assert "invented.example" not in content
    assert "PyTorch" in content


@pytest.mark.asyncio
async def test_a_bare_domain_url_in_a_tool_reason_never_reaches_a_section():
    leaky = json.dumps(
        {
            "tools": [
                {
                    "stage": "modelling",
                    "name": "PyTorch",
                    "reason": "See github.com/huggingface/transformers for weights.",
                    "alternatives": ["JAX"],
                }
            ]
        }
    )
    dependencies = _dependencies(
        FakeGateway(leaky), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    content = update["sections"][0]["content"]
    assert "github.com" not in content
    assert "PyTorch" in content
    assert "See" in content
    assert "for weights" in content


@pytest.mark.asyncio
async def test_a_bare_domain_url_in_an_alternative_never_reaches_a_section():
    leaky = json.dumps(
        {
            "tools": [
                {
                    "stage": "modelling",
                    "name": "PyTorch",
                    "reason": "A solid default for training.",
                    "alternatives": ["JAX (pytorch.org/docs)"],
                }
            ]
        }
    )
    dependencies = _dependencies(
        FakeGateway(leaky), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    content = update["sections"][0]["content"]
    assert "pytorch.org" not in content
    assert "JAX" in content


@pytest.mark.asyncio
async def test_url_stripping_does_not_eat_filenames_that_look_like_domains():
    safe = json.dumps(
        {
            "tools": [
                {
                    "stage": "modelling",
                    "name": "PyTorch",
                    "reason": (
                        "Fine-tune the released ResNet-50.pt checkpoint using config.yaml."
                    ),
                    "alternatives": ["JAX"],
                }
            ]
        }
    )
    dependencies = _dependencies(
        FakeGateway(safe), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    content = update["sections"][0]["content"]
    assert "ResNet-50.pt" in content
    assert "config.yaml" in content


@pytest.mark.asyncio
async def test_an_unparseable_tools_response_fails_the_node():
    dependencies = _dependencies(
        FakeGateway("not json at all"), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    with pytest.raises(RuntimeError, match="Could not parse the tool-recommendation response"):
        await recommend_tools_node(
            {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
        )
