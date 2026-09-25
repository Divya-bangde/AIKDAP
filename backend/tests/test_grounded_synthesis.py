"""Sprint 9E: grounded RAG synthesis through the AIKDAP LLM gateway.

Every test here is offline and deterministic. Nothing reaches Gemini:
where a model is involved, `litellm.acompletion` itself is replaced, so
the call still travels the *real* gateway (credentials, error mapping,
secret scrubbing, response normalization) and only the network hop is
faked. That is deliberate — a test that stubbed out `LLMGateway`
wholesale would pass just as happily if synthesis had been rewired to
call a provider directly, which is exactly the regression this sprint
must prevent.

The load-bearing assertions are the negative ones: that only retrieved
evidence reaches the model, that a citation id the model invented is
rejected rather than repaired, and that a model failure produces no
answer at all rather than an uncited one.
"""

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.planner.nodes import build_citation, synthesis_node
from app.agents.planner.state import PROVENANCE_KEYS
from app.agents.planner.synthesis import (
    ExtractiveSynthesizer,
    GroundedSynthesizer,
    SynthesisResponseError,
    get_synthesizer,
)
from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.gateway import LLMGateway, LLMProviderError
from app.modules.research.enums import ResearchGroundingStatus

SYNTHESIS_MODEL = "gemini/gemini-flash-latest"

#: Stands in for the Sprint 9C/9D pipeline's output: real chunk
#: identity, both stage scores, nothing simulated.
CHUNK_ID = "11111111-1111-4111-8111-111111111111"
ASSET_ID = "22222222-2222-4222-8222-222222222222"


def asset_document(
    index: int, snippet: str, *, rerank_score: float | None = None
) -> dict:
    """One evidence item shaped exactly as `SemanticAssetRetriever` emits."""
    document = {
        "source": "asset",
        "provider": "knowledge_base_semantic_v1",
        "reference": f"asset:{ASSET_ID}#chunk-{index}",
        "title": f"ABC Poultry FY2025 (chunk {index})",
        "snippet": snippet,
        "score": round(0.9 - index * 0.05, 4),
        "simulated": False,
        "chunk_id": f"{CHUNK_ID[:-1]}{index}",
        "asset_id": ASSET_ID,
        "file_name": "abc_poultry.txt",
        "rank": index + 1,
        "retrieval_rank": index + 1,
        "retrieval_score": round(0.9 - index * 0.05, 6),
        "reranking_status": "completed" if rerank_score is not None else "unavailable",
    }
    if rerank_score is not None:
        document["rerank_score"] = rerank_score
    return document


def simulated_document(index: int) -> dict:
    """A placeholder from the mock web provider."""
    return {
        "source": "web",
        "provider": "mock_web_v1",
        "reference": f"mock://web-research/poultry/{index}",
        "title": f"Simulated external reference {index}",
        "snippet": "SIMULATED RESULT — no external search provider is configured.",
        "score": 0.5,
        "simulated": True,
        "rank": index,
    }


def citations_from(documents: list[dict]) -> list[dict]:
    """Key documents the way the context builder does."""
    return [build_citation(doc, i) for i, doc in enumerate(documents, start=1)]


def model_reply(
    answer: str = "ABC Poultry produced 1.2 million tonnes of poultry feed in FY2025 [c1].",
    *,
    citation_ids: list[str] | None = None,
    grounding_status: str = "grounded",
    raw: str | None = None,
) -> SimpleNamespace:
    """Build a LiteLLM-shaped response carrying a synthesis JSON payload."""
    import json

    content = raw
    if content is None:
        content = json.dumps(
            {
                "answer": answer,
                "citation_ids": ["c1"] if citation_ids is None else citation_ids,
                "grounding_status": grounding_status,
            }
        )
    return SimpleNamespace(
        model=SYNTHESIS_MODEL,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(
            model_dump=lambda: {
                "prompt_tokens": 320,
                "completion_tokens": 64,
                "total_tokens": 384,
            }
        ),
    )


@pytest.fixture
def litellm_call(monkeypatch) -> AsyncMock:
    """Replace LiteLLM's entry point, keeping the real gateway in the path.

    This is the seam that proves the call chain: anything the
    synthesizer sends arrives here having passed through
    `LLMGateway._call`, or it does not arrive at all.
    """
    mock = AsyncMock(return_value=model_reply())
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    return mock


@pytest.fixture
def synthesizer() -> GroundedSynthesizer:
    """A grounded synthesizer wired to a real gateway instance."""
    return GroundedSynthesizer(gateway=LLMGateway(), model=SYNTHESIS_MODEL)


async def synthesize(synth, documents, *, query="How much feed?", warnings=None):
    """Run a synthesizer over documents the way the graph would."""
    citations = citations_from(documents)
    return await synth.synthesize(
        query=query,
        objective="Answer the question from the knowledge base.",
        context="rendered separately by the context builder",
        documents=documents,
        citations=citations,
        warnings=warnings or [],
    )


def sent_messages(litellm_call) -> list[dict]:
    """The messages that actually reached LiteLLM."""
    return litellm_call.await_args.kwargs["messages"]


def sent_text(litellm_call) -> str:
    """Everything sent to the model, concatenated."""
    return "\n".join(message["content"] for message in sent_messages(litellm_call))


# ---------------------------------------------------------------------------
# TEST 1 — retrieved evidence reaches synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieved_evidence_reaches_synthesis(synthesizer, litellm_call):
    """Every retrieved snippet must appear in the model's prompt."""
    documents = [
        asset_document(0, "ABC Poultry produced 1.2 million tonnes of feed in FY2025."),
        asset_document(1, "Feed cost inflation is a major challenge."),
    ]

    result = await synthesize(synthesizer, documents)

    prompt = sent_text(litellm_call)
    for document in documents:
        assert document["snippet"] in prompt
    assert result.evidence_supplied == 2


# ---------------------------------------------------------------------------
# TEST 2 — only retrieved evidence is passed to the model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_retrieved_evidence_is_sent(synthesizer, litellm_call):
    """Nothing beyond the question, objective, and evidence may be sent."""
    documents = [asset_document(0, "ABC Poultry operates in Maharashtra.")]
    unrelated = "Another project's confidential revenue figures."

    await synthesize(synthesizer, documents, query="Where does ABC Poultry operate?")

    prompt = sent_text(litellm_call)
    assert unrelated not in prompt
    # Exactly two messages: the grounding system prompt and the request.
    messages = sent_messages(litellm_call)
    assert [message["role"] for message in messages] == ["system", "user"]
    # The evidence the model may cite is exactly what was supplied.
    assert "[c1]" in prompt
    assert "[c2]" not in prompt


@pytest.mark.asyncio
async def test_simulated_evidence_is_withheld_from_the_model(
    synthesizer, litellm_call
):
    """Placeholder web results must never become groundable evidence."""
    documents = [
        asset_document(0, "ABC Poultry produced 1.2 million tonnes of feed."),
        simulated_document(1),
    ]

    result = await synthesize(synthesizer, documents)

    prompt = sent_text(litellm_call)
    assert "SIMULATED RESULT" not in prompt
    assert result.evidence_supplied == 1
    # Withholding is disclosed in the answer, not silently dropped.
    assert "simulated" in result.answer.lower()


# ---------------------------------------------------------------------------
# TEST 3 — synthesis calls the existing AIKDAP gateway, which calls LiteLLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_goes_through_the_gateway_to_litellm(litellm_call):
    """The chain Synthesis -> LLMGateway -> LiteLLM must be the real one.

    The gateway is a genuine `LLMGateway`; only LiteLLM's entry point is
    replaced. A request arriving here therefore proves it was assembled
    by the gateway — including the model id and the generation settings
    the gateway owns.
    """
    calls: list[str] = []

    class RecordingGateway(LLMGateway):
        """A real gateway that notes when it was used."""

        async def generate(self, **kwargs):
            calls.append(kwargs["model"])
            return await super().generate(**kwargs)

    synth = GroundedSynthesizer(gateway=RecordingGateway(), model=SYNTHESIS_MODEL)
    await synthesize(synth, [asset_document(0, "ABC Poultry produced 1.2m tonnes.")])

    assert calls == [SYNTHESIS_MODEL], "synthesis must call the gateway"
    litellm_call.assert_awaited_once()
    request = litellm_call.await_args.kwargs
    assert request["model"] == SYNTHESIS_MODEL
    # Settings the gateway applies, proving the request was built there
    # rather than handed to LiteLLM directly by the synthesizer.
    assert request["temperature"] == settings.llm_temperature
    assert request["max_tokens"] == settings.llm_max_tokens
    assert request["timeout"] == settings.llm_timeout


# ---------------------------------------------------------------------------
# TEST 4 — no direct provider SDK anywhere in the research pipeline
# ---------------------------------------------------------------------------


#: Modules nothing outside `app.core.llm` may import. `litellm` is
#: included: the gateway is the only permitted place it appears.
FORBIDDEN_IMPORT_ROOTS = {
    "litellm",
    "google",
    "google_genai",
    "google.generativeai",
    "vertexai",
    "openai",
    "anthropic",
}

BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: The research pipeline: agents, the research module, and the workers
#: that drive them.
PIPELINE_PACKAGES = (
    BACKEND_ROOT / "app" / "agents",
    BACKEND_ROOT / "app" / "modules" / "research",
    BACKEND_ROOT / "app" / "modules" / "knowledge_base",
)


def imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_research_pipeline_never_imports_a_provider_sdk():
    """Static proof that no provider client is reachable from the pipeline.

    Stronger than asserting on one call: this holds for every module in
    the research pipeline, so a future edit that adds `import litellm`
    to a synthesis node fails here even if its own tests pass.
    """
    offenders: dict[str, set[str]] = {}
    for package in PIPELINE_PACKAGES:
        for path in package.rglob("*.py"):
            forbidden = imported_roots(path) & FORBIDDEN_IMPORT_ROOTS
            if forbidden:
                offenders[str(path.relative_to(BACKEND_ROOT))] = forbidden

    assert not offenders, f"provider SDK imported outside the gateway: {offenders}"


def test_litellm_is_imported_only_by_the_gateway():
    """The containment claim in `app.core.llm` must actually hold."""
    importers = {
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in (BACKEND_ROOT / "app").rglob("*.py")
        if "litellm" in imported_roots(path)
    }
    assert importers == {"app/core/llm/gateway.py"}


# ---------------------------------------------------------------------------
# TEST 5 & 6 — citation validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_citation_ids_are_accepted(synthesizer, litellm_call):
    """Ids that were supplied must resolve to the exact evidence items."""
    documents = [
        asset_document(0, "ABC Poultry produced 1.2 million tonnes of feed."),
        asset_document(1, "The company operates in Maharashtra."),
    ]
    litellm_call.return_value = model_reply(citation_ids=["c1", "c2"])

    result = await synthesize(synthesizer, documents)

    assert [c["id"] for c in result.citations] == ["c1", "c2"]
    assert result.rejected_citation_ids == []
    assert result.grounding_status is ResearchGroundingStatus.GROUNDED


@pytest.mark.asyncio
async def test_unknown_citation_ids_are_rejected(synthesizer, litellm_call):
    """A fabricated id must be dropped, never repaired into a real one."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply(citation_ids=["c1", "fake-id", "c99"])

    result = await synthesize(synthesizer, documents)

    assert [c["id"] for c in result.citations] == ["c1"]
    assert result.rejected_citation_ids == ["fake-id", "c99"]
    # A partially invented citation set is not a grounded answer.
    assert result.grounding_status is ResearchGroundingStatus.PARTIALLY_GROUNDED
    # The model's inline `[fake-id]` marker survives in its own prose,
    # so the reader must be told it resolves to nothing.
    assert "Unverified citations" in result.answer
    assert "fake-id" in result.answer.split("Unverified citations")[1]


@pytest.mark.asyncio
async def test_wholly_invented_citations_leave_no_citation_behind(
    synthesizer, litellm_call
):
    """If every id was invented, the result carries none of them."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply(citation_ids=["fake-id"])

    result = await synthesize(synthesizer, documents)

    assert result.citations == []
    assert result.rejected_citation_ids == ["fake-id"]
    # Nothing valid was cited, so the answer is not grounded — whatever
    # the model claimed about itself.
    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# TEST 7 & 8 — supported and unsupported questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supported_question_produces_a_grounded_result(
    synthesizer, litellm_call
):
    """A question the evidence answers yields a grounded, cited answer."""
    documents = [
        asset_document(0, "ABC Poultry produced 1.2 million tonnes of feed in FY2025.")
    ]

    result = await synthesize(
        synthesizer, documents, query="How much poultry feed was produced in FY2025?"
    )

    assert result.grounding_status is ResearchGroundingStatus.GROUNDED
    assert "1.2 million tonnes" in result.answer
    assert len(result.citations) == 1


@pytest.mark.asyncio
async def test_unsupported_question_reports_insufficient_evidence(
    synthesizer, litellm_call
):
    """A model reporting it cannot answer is believed, and cites nothing."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply(
        answer="The evidence does not state ABC Poultry's net profit.",
        citation_ids=[],
        grounding_status="insufficient_evidence",
    )

    result = await synthesize(
        synthesizer, documents, query="What was the exact net profit in FY2025?"
    )

    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    assert result.citations == []


@pytest.mark.asyncio
async def test_model_cannot_upgrade_its_own_grounding_status(
    synthesizer, litellm_call
):
    """A model claiming 'grounded' while citing nothing is not believed."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply(
        answer="ABC Poultry earned 500 crore in profit.",
        citation_ids=[],
        grounding_status="grounded",
    )

    result = await synthesize(synthesizer, documents)

    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# TEST 9 — empty evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_evidence_never_reaches_the_model(synthesizer, litellm_call):
    """With nothing to ground in, no model call is made at all."""
    result = await synthesize(synthesizer, [])

    litellm_call.assert_not_awaited()
    assert result.citations == []
    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    assert "insufficient" in result.answer.lower()


@pytest.mark.asyncio
async def test_only_simulated_evidence_never_reaches_the_model(
    synthesizer, litellm_call
):
    """Placeholders alone are the same as no evidence."""
    result = await synthesize(synthesizer, [simulated_document(1)])

    litellm_call.assert_not_awaited()
    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# TEST 10 & 11 — reranking availability is transparent to synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_works_when_reranking_is_unavailable(
    synthesizer, litellm_call
):
    """Stage-1-only evidence is valid evidence and must be usable."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    assert documents[0]["reranking_status"] == "unavailable"
    assert "rerank_score" not in documents[0]

    result = await synthesize(synthesizer, documents)

    assert result.grounding_status is ResearchGroundingStatus.GROUNDED
    assert result.citations[0]["reranking_status"] == "unavailable"
    # A measurement that never happened is absent, not zero.
    assert "rerank_score" not in result.citations[0]


@pytest.mark.asyncio
async def test_reranked_evidence_is_used_when_available(synthesizer, litellm_call):
    """Reranked evidence flows through unchanged, in the order supplied."""
    documents = [
        asset_document(0, "Feed cost inflation is a major challenge.", rerank_score=7.5),
        asset_document(1, "ABC Poultry operates in Maharashtra.", rerank_score=2.1),
    ]
    litellm_call.return_value = model_reply(citation_ids=["c1", "c2"])

    result = await synthesize(synthesizer, documents)

    assert [c["rerank_score"] for c in result.citations] == [7.5, 2.1]
    assert all(c["reranking_status"] == "completed" for c in result.citations)
    # The prompt reflects the reranked order it was given.
    prompt = sent_text(litellm_call)
    assert prompt.index("Feed cost inflation") < prompt.index("Maharashtra")


# ---------------------------------------------------------------------------
# TEST 12-15 — evidence identity survives retrieval -> context -> synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scores_and_identity_survive_into_the_citations(
    synthesizer, litellm_call
):
    """chunk_id, asset_id, and both scores must reach the final citation."""
    document = asset_document(0, "ABC Poultry produced 1.2 million tonnes.", rerank_score=4.2)
    litellm_call.return_value = model_reply(citation_ids=["c1"])

    result = await synthesize(synthesizer, [document])
    citation = result.citations[0]

    # TEST 14 / TEST 15 — identity
    assert citation["chunk_id"] == document["chunk_id"]
    assert citation["asset_id"] == ASSET_ID
    assert citation["file_name"] == "abc_poultry.txt"
    # TEST 12 / TEST 13 — both scores, unmodified and not conflated
    assert citation["retrieval_score"] == document["retrieval_score"]
    assert citation["rerank_score"] == 4.2
    assert citation["retrieval_rank"] == document["retrieval_rank"]
    assert citation["retrieval_score"] != citation["rerank_score"]


def test_build_citation_copies_every_declared_provenance_field():
    """No provenance field may be silently dropped when keying evidence."""
    document = asset_document(0, "text", rerank_score=1.0)
    citation = build_citation(document, 1)

    for key in PROVENANCE_KEYS:
        if key in document:
            assert citation[key] == document[key], f"{key} was lost"


# ---------------------------------------------------------------------------
# TEST 16 — the context budget excludes evidence, and excluded evidence
#           cannot be cited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_excluded_evidence_is_neither_sent_nor_citable(litellm_call):
    """Evidence dropped for budget must be invisible and uncitable."""
    documents = [
        asset_document(0, "A" * 400 + " first evidence item."),
        asset_document(1, "B" * 400 + " second evidence item."),
        asset_document(2, "SECRET-EXCLUDED-MARKER third evidence item."),
    ]
    # A budget that admits the first item only.
    synth = GroundedSynthesizer(
        gateway=LLMGateway(), model=SYNTHESIS_MODEL, max_evidence_characters=300
    )
    # The model tries to cite an item it was never given.
    litellm_call.return_value = model_reply(citation_ids=["c1", "c3"])

    result = await synthesize(synth, documents)

    prompt = sent_text(litellm_call)
    assert "SECRET-EXCLUDED-MARKER" not in prompt
    assert result.evidence_supplied == 1
    assert [c["id"] for c in result.citations] == ["c1"]
    assert result.rejected_citation_ids == ["c3"]


# ---------------------------------------------------------------------------
# TEST 17 — a model failure produces no answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_failure_produces_no_answer(synthesizer, litellm_call):
    """A gateway error must propagate, not become an uncited answer."""
    litellm_call.side_effect = RuntimeError("gemini exploded")

    with pytest.raises(LLMProviderError):
        await synthesize(
            synthesizer, [asset_document(0, "ABC Poultry produced 1.2m tonnes.")]
        )


@pytest.mark.asyncio
async def test_unparseable_response_produces_no_answer(synthesizer, litellm_call):
    """Prose where JSON was required is unusable, not a free-text answer.

    Accepting it would mean an answer whose claim-to-citation mapping is
    unknown — precisely what citation validation exists to rule out.
    """
    litellm_call.return_value = model_reply(raw="Sure! Here is my answer: 1.2 million.")

    with pytest.raises(SynthesisResponseError):
        await synthesize(
            synthesizer, [asset_document(0, "ABC Poultry produced 1.2m tonnes.")]
        )


@pytest.mark.asyncio
async def test_empty_model_answer_is_rejected(synthesizer, litellm_call):
    """A well-formed envelope with no answer is still no answer."""
    litellm_call.return_value = model_reply(answer="", citation_ids=["c1"])

    with pytest.raises(SynthesisResponseError):
        await synthesize(
            synthesizer, [asset_document(0, "ABC Poultry produced 1.2m tonnes.")]
        )


@pytest.mark.asyncio
async def test_fenced_json_is_accepted(synthesizer, litellm_call):
    """A ```json fence is a formatting quirk, not an unusable response."""
    litellm_call.return_value = model_reply(
        raw='```json\n{"answer": "1.2 million tonnes [c1].", '
        '"citation_ids": ["c1"], "grounding_status": "grounded"}\n```'
    )

    result = await synthesize(
        synthesizer, [asset_document(0, "ABC Poultry produced 1.2m tonnes.")]
    )

    assert result.grounding_status is ResearchGroundingStatus.GROUNDED
    assert [c["id"] for c in result.citations] == ["c1"]


# ---------------------------------------------------------------------------
# TEST 18-20 — the synthesis node carries it all into the trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_node_records_grounding_and_model_metadata(litellm_call):
    """The node's state update and step output must carry the verdict."""
    from app.agents.planner.nodes import GraphDependencies
    from app.agents.planner.planner import get_planner

    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply(citation_ids=["c1", "ghost"])

    from app.core.llm.gateway import LLMGateway
    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=None,
        web_provider=None,
        synthesizer=GroundedSynthesizer(gateway=LLMGateway(), model=SYNTHESIS_MODEL),
        llm_gateway=LLMGateway(),
    )
    state = {
        "run_id": str(uuid.uuid4()),
        "query": "How much feed?",
        "objective": "Answer from the knowledge base.",
        "context": "built by the context builder",
        "retrieved_documents": documents,
        "citations": citations_from(documents),
    }

    update = await synthesis_node(state, {"configurable": {"dependencies": dependencies}})

    # TEST 18 — the answer is in the state the service persists.
    assert update["final_answer"]
    assert update["grounding_status"] == "partially_grounded"
    # TEST 19 — structured citations, not strings.
    assert isinstance(update["citations"][0], dict)
    assert update["citations"][0]["chunk_id"]
    # TEST 20 — the step records how synthesis actually executed.
    output = update["step"]["output"]
    assert output["grounding_status"] == "partially_grounded"
    assert output["evidence_supplied"] == 1
    assert output["rejected_citation_ids"] == ["ghost"]
    assert output["model"] == SYNTHESIS_MODEL
    assert output["provider"] == "gemini"
    assert isinstance(output["latency_ms"], int)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_get_synthesizer_honours_configuration(monkeypatch):
    """The strategy is chosen by configuration, with no silent downgrade."""
    monkeypatch.setattr(settings, "synthesis_grounded", True)
    assert isinstance(get_synthesizer(), GroundedSynthesizer)

    monkeypatch.setattr(settings, "synthesis_grounded", False)
    assert isinstance(get_synthesizer(), ExtractiveSynthesizer)


def test_grounded_synthesizer_reads_its_model_from_settings(monkeypatch):
    """The model must never be hardcoded in the synthesis layer."""
    monkeypatch.setattr(settings, "synthesis_model", "gemini/some-other-model")
    assert GroundedSynthesizer().model == "gemini/some-other-model"


@pytest.mark.asyncio
async def test_extractive_path_still_reports_a_grounding_status():
    """The offline synthesizer must satisfy the same contract."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    result = await synthesize(ExtractiveSynthesizer(), documents)

    assert result.grounding_status is ResearchGroundingStatus.GROUNDED
    assert result.model is None

    simulated_only = await synthesize(ExtractiveSynthesizer(), [simulated_document(1)])
    assert (
        simulated_only.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    )


# ---------------------------------------------------------------------------
# TEST 21 — ownership travels into retrieval, not around it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_retrieval_scopes_the_search_to_the_run_owner():
    """The node must pass the run's owner into the retriever.

    Sprint 9C already proves `two_stage_search` refuses another user's
    project and joins on `owner_id`. What is new in 9E is the glue: if
    the node dropped the owner, that enforcement would be bypassed
    while every existing test still passed.
    """
    from app.agents.planner.nodes import GraphDependencies, asset_retrieval_node
    from app.agents.planner.planner import get_planner

    seen: dict = {}

    class RecordingRetriever:
        name = "recording"

        async def retrieve(self, *, owner_id, project_id, query, limit, asset_id=None):
            seen.update(owner_id=owner_id, project_id=project_id, limit=limit)
            return []

    owner_id = uuid.uuid4()
    project_id = uuid.uuid4()
    from app.core.llm.gateway import LLMGateway
    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=RecordingRetriever(),
        web_provider=None,
        synthesizer=ExtractiveSynthesizer(),
        llm_gateway=LLMGateway(),
    )

    await asset_retrieval_node(
        {
            "run_id": str(uuid.uuid4()),
            "owner_id": str(owner_id),
            "project_id": str(project_id),
            "query": "anything",
            "max_results": 5,
        },
        {"configurable": {"dependencies": dependencies}},
    )

    assert seen["owner_id"] == owner_id
    assert seen["project_id"] == project_id


@pytest.mark.asyncio
async def test_semantic_retriever_forwards_ownership_to_the_knowledge_base():
    """The retriever must scope the search itself, not filter afterwards."""
    from app.agents.planner.nodes import SemanticAssetRetriever
    from app.modules.knowledge_base.reranking import RerankingStatus
    from app.modules.knowledge_base.service import SemanticSearchOutcome

    captured: dict = {}
    retriever = SemanticAssetRetriever.__new__(SemanticAssetRetriever)

    class FakeService:
        async def two_stage_search(self, owner_id, **kwargs):
            captured.update(owner_id=owner_id, **kwargs)
            return SemanticSearchOutcome(
                hits=[],
                reranking_status=RerankingStatus.UNAVAILABLE,
                candidate_count=0,
            )

    retriever._service = FakeService()
    retriever._assets = None

    owner_id = uuid.uuid4()
    project_id = uuid.uuid4()
    await retriever.retrieve(
        owner_id=owner_id, project_id=project_id, query="feed production", limit=5
    )

    assert captured["owner_id"] == owner_id
    assert captured["project_id"] == project_id
    # Over-retrieval for the reranker, derived from `limit` rather than
    # hardcoded at the call site.
    assert captured["top_k"] == 5
    assert captured["candidate_k"] > captured["top_k"]
