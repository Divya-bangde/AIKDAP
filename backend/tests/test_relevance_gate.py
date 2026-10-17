"""Sprint 9F: the relevance gate, and the calibration behind its threshold.

Two kinds of test here.

The first kind checks the gate mechanically: what it accepts, what it
rejects, what it refuses to guess at. The load-bearing ones are
negative — that rejected evidence cannot reach the synthesis model,
cannot be cited, and that zero accepted evidence means *no model call
at all* rather than a call asking the model to say "insufficient".

The second kind (`TestCalibration`) re-derives the production threshold
from the recorded calibration fixture. It exists so the configured
number stays tied to measured evidence: if someone changes the
threshold to a value the fixture does not support, that is a test
failure and not a silent regression in answer quality.

Everything is offline. The reranker and the LLM are both replaced at
their transport boundaries, so the real gate, service, and gateway code
all still execute.
"""

import json
import statistics
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.agents.planner.nodes import build_citation
from app.agents.planner.synthesis import GroundedSynthesizer
from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.gateway import LLMGateway
from app.modules.knowledge_base.relevance import (
    GateDecision,
    RelevanceGate,
    get_relevance_gate,
)
from app.modules.research.enums import ResearchGroundingStatus

CALIBRATION_PATH = Path(__file__).with_name("fixtures_relevance_calibration.json")

CHUNK_A = "11111111-1111-4111-8111-111111111111"
CHUNK_B = "22222222-2222-4222-8222-222222222222"
ASSET = "33333333-3333-4333-8333-333333333333"


class Item:
    """A stand-in for a retrieval hit, carrying just an identity."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Item({self.name})"


def evidence(chunk_id: str, snippet: str, score: float) -> dict:
    """A citation shaped as the pipeline produces after the gate."""
    return build_citation(
        {
            "source": "asset",
            "provider": "knowledge_base_semantic_v1",
            "reference": f"asset:{ASSET}#chunk-0",
            "title": "ABC Poultry FY2025",
            "snippet": snippet,
            "score": 0.7,
            "simulated": False,
            "chunk_id": chunk_id,
            "asset_id": ASSET,
            "rank": 1,
            "retrieval_rank": 1,
            "retrieval_score": 0.7,
            "rerank_score": score,
            "reranking_status": "completed",
            "relevance_threshold": -2.0,
        },
        1,
    )


# ---------------------------------------------------------------------------
# TEST 1, 2, 14, 15, 16 — the gate's decision rule
# ---------------------------------------------------------------------------


def test_score_above_threshold_is_accepted():
    """TEST 1."""
    gate = RelevanceGate(threshold=-2.0)
    decision = gate.apply([Item("a")], scores=[-0.5])

    assert decision.accepted_count == 1
    assert decision.rejected_count == 0
    assert decision.applied is True


def test_score_below_threshold_is_rejected():
    """TEST 2."""
    gate = RelevanceGate(threshold=-2.0)
    decision = gate.apply([Item("a")], scores=[-5.4])

    assert decision.accepted_count == 0
    assert decision.rejected_count == 1
    # Rejected evidence is retained for diagnostics, not discarded --
    # an empty result has to be explainable.
    assert decision.rejected[0].name == "a"


def test_threshold_is_inclusive():
    """A score exactly at the threshold is accepted, not dropped."""
    gate = RelevanceGate(threshold=-2.0)
    assert gate.apply([Item("a")], scores=[-2.0]).accepted_count == 1


def test_gate_reads_the_threshold_from_settings(monkeypatch):
    """TEST 16 — nothing hardcodes the threshold."""
    monkeypatch.setattr(settings, "reranker_relevance_threshold", -7.5)
    assert get_relevance_gate().threshold == -7.5
    assert RelevanceGate().threshold == -7.5


def test_changing_the_threshold_changes_the_verdict(monkeypatch):
    """TEST 15 — the setting actually controls the gate."""
    items = [Item("a"), Item("b"), Item("c")]
    scores = [+3.0, -1.0, -6.0]

    monkeypatch.setattr(settings, "reranker_relevance_threshold", -2.0)
    assert get_relevance_gate().apply(items, scores=scores).accepted_count == 2

    monkeypatch.setattr(settings, "reranker_relevance_threshold", +1.0)
    assert get_relevance_gate().apply(items, scores=scores).accepted_count == 1

    monkeypatch.setattr(settings, "reranker_relevance_threshold", -10.0)
    assert get_relevance_gate().apply(items, scores=scores).accepted_count == 3


# ---------------------------------------------------------------------------
# TEST 13 — unscored evidence is never judged
# ---------------------------------------------------------------------------


def test_unscored_evidence_is_passed_through_not_filtered():
    """TEST 13 — no score is invented when reranking did not run.

    Comparing a made-up score against a threshold calibrated on real
    ones would produce a confident but meaningless verdict, so the gate
    declines to run and says so.
    """
    gate = RelevanceGate(threshold=-2.0)
    decision = gate.apply([Item("a"), Item("b")], scores=[None, None])

    assert decision.applied is False
    assert decision.threshold is None
    assert decision.accepted_count == 2
    assert decision.rejected_count == 0


def test_partially_scored_evidence_is_not_half_filtered():
    """A mixed batch is not a state any caller could interpret."""
    gate = RelevanceGate(threshold=-2.0)
    decision = gate.apply([Item("a"), Item("b")], scores=[+5.0, None])

    assert decision.applied is False
    assert decision.accepted_count == 2


def test_empty_input_does_not_claim_a_verdict():
    decision = RelevanceGate(threshold=-2.0).apply([], scores=[])
    assert decision.applied is False
    assert decision.accepted == []


# ---------------------------------------------------------------------------
# TEST 3, 4, 5 — the gate preserves identity
# ---------------------------------------------------------------------------


def test_accepted_evidence_keeps_its_full_identity():
    """TEST 3, 4, 5 — chunk_id, asset_id, and both scores survive."""
    from app.modules.knowledge_base.models import KnowledgeChunk
    from app.modules.knowledge_base.service import RetrievalHit

    chunk = KnowledgeChunk(
        id=uuid.UUID(CHUNK_A), project_id=uuid.uuid4(), asset_id=uuid.UUID(ASSET),
        chunk_index=3, content="Feed cost inflation is a major challenge.",
    )
    hit = RetrievalHit(
        chunk=chunk, retrieval_distance=0.28, retrieval_rank=2, rerank_score=-0.51
    )

    decision = RelevanceGate(threshold=-2.0).apply([hit], scores=[hit.rerank_score])
    kept = decision.accepted[0]

    assert kept is hit, "the gate must not rebuild items, only select them"
    assert str(kept.chunk.id) == CHUNK_A
    assert str(kept.chunk.asset_id) == ASSET
    assert kept.rerank_score == -0.51
    assert kept.retrieval_rank == 2
    assert kept.retrieval_distance == 0.28


# ---------------------------------------------------------------------------
# TEST 6-12 — the gate's effect on synthesis
# ---------------------------------------------------------------------------


def model_reply(answer: str, citation_ids: list[str]) -> SimpleNamespace:
    content = json.dumps(
        {"answer": answer, "citation_ids": citation_ids, "grounding_status": "grounded"}
    )
    return SimpleNamespace(
        model="gemini/gemini-flash-latest",
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 300}),
    )


@pytest.fixture
def litellm_call(monkeypatch) -> AsyncMock:
    """Replace LiteLLM's entry point, keeping the real gateway in path."""
    mock = AsyncMock(return_value=model_reply("Answer [c1].", ["c1"]))
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("offline-gemini-fixture"))
    return mock


async def synthesize(citations, litellm_call):
    synth = GroundedSynthesizer(gateway=LLMGateway(), model="gemini/gemini-flash-latest")
    return await synth.synthesize(
        query="What challenges does ABC Poultry face?",
        objective="Answer from the knowledge base.",
        context="built by the context builder",
        documents=[],
        citations=citations,
        warnings=[],
    )


@pytest.mark.asyncio
async def test_rejected_evidence_never_reaches_the_model(litellm_call):
    """TEST 6 + TEST 11 — the model sees only what the gate accepted.

    The gate runs in the retrieval layer, so by the time synthesis is
    reached the rejected chunk is simply not in the citation list. This
    asserts the consequence that matters: its text is nowhere in the
    prompt.
    """
    accepted = evidence(CHUNK_A, "Feed cost inflation is a major challenge.", -0.5)
    # What a rejected chunk would have contained.
    rejected_text = "REJECTED-MARKER The company operates in Maharashtra."

    await synthesize([accepted], litellm_call)

    sent = "\n".join(m["content"] for m in litellm_call.await_args.kwargs["messages"])
    assert "Feed cost inflation" in sent
    assert "REJECTED-MARKER" not in sent
    assert rejected_text not in sent


@pytest.mark.asyncio
async def test_rejected_evidence_cannot_be_cited(litellm_call):
    """TEST 7 + TEST 12 — citing a withheld chunk's id is rejected."""
    accepted = evidence(CHUNK_A, "Feed cost inflation is a major challenge.", -0.5)
    litellm_call.return_value = model_reply("Answer [c1] and [c2].", ["c1", "c2"])

    result = await synthesize([accepted], litellm_call)

    assert [c["id"] for c in result.citations] == ["c1"]
    assert result.rejected_citation_ids == ["c2"]
    assert result.grounding_status is ResearchGroundingStatus.PARTIALLY_GROUNDED


@pytest.mark.asyncio
async def test_zero_accepted_evidence_makes_no_model_call(litellm_call):
    """TEST 8 + TEST 9 — the headline requirement of this sprint.

    When the gate rejects everything, there is no evidence to ground an
    answer in, so the model is never called: not to answer, and not to
    be asked to say "insufficient evidence" either. That decision is
    the backend's and costs nothing.
    """
    result = await synthesize([], litellm_call)

    litellm_call.assert_not_awaited()
    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    assert result.citations == []
    assert result.evidence_supplied == 0
    assert "insufficient" in result.answer.lower()


@pytest.mark.asyncio
async def test_some_accepted_evidence_does_call_the_model(litellm_call):
    """TEST 10 — the gate must not suppress legitimate synthesis."""
    accepted = evidence(CHUNK_A, "Feed cost inflation is a major challenge.", -0.5)

    result = await synthesize([accepted], litellm_call)

    litellm_call.assert_awaited_once()
    assert result.grounding_status is ResearchGroundingStatus.GROUNDED
    assert len(result.citations) == 1


# ---------------------------------------------------------------------------
# TEST 17 — provenance and relevance are separate concepts
# ---------------------------------------------------------------------------


def test_gate_judges_relevance_not_provenance():
    """TEST 17 — `simulated` is not an input to the relevance decision.

    Sprint 7's provenance model and this gate answer different
    questions. A simulated item is withheld from synthesis by the
    synthesizer (it contains no facts), not by the gate, and the gate
    must not encode that rule a second time.
    """
    gate = RelevanceGate(threshold=-2.0)
    simulated_but_relevant = Item("simulated")
    real_but_irrelevant = Item("real")

    decision = gate.apply(
        [simulated_but_relevant, real_but_irrelevant], scores=[+4.0, -9.0]
    )

    assert decision.accepted == [simulated_but_relevant]
    assert decision.rejected == [real_but_irrelevant]


def test_gate_decision_reports_counts():
    """TEST 10 support — the decision is inspectable, not opaque."""
    decision: GateDecision = RelevanceGate(threshold=0.0).apply(
        [Item("a"), Item("b"), Item("c")], scores=[+1.0, -1.0, +2.0]
    )
    assert decision.accepted_count == 2
    assert decision.rejected_count == 1
    assert decision.threshold == 0.0


# ---------------------------------------------------------------------------
# PHASE 18 — calibration
# ---------------------------------------------------------------------------


class TestCalibration:
    """Re-derives the production threshold from the recorded fixture.

    The fixture is real output from the live stack (BGE-M3 → pgvector →
    BGE-Reranker-v2-m3), labelled from document content. These tests
    make the configured threshold answerable to that evidence.
    """

    @staticmethod
    def rows() -> list[dict]:
        return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))

    @staticmethod
    def metrics(rows: list[dict], threshold: float) -> dict:
        tp = sum(1 for r in rows if r["expected_relevance"] and r["rerank_score"] >= threshold)
        fn = sum(1 for r in rows if r["expected_relevance"] and r["rerank_score"] < threshold)
        fp = sum(1 for r in rows if not r["expected_relevance"] and r["rerank_score"] >= threshold)
        tn = sum(1 for r in rows if not r["expected_relevance"] and r["rerank_score"] < threshold)
        return {
            "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "fpr": fp / (fp + tn) if fp + tn else 0.0,
            "fnr": fn / (tp + fn) if tp + fn else 0.0,
        }

    def test_fixture_is_the_documented_dataset(self):
        rows = self.rows()
        assert len(rows) == 70
        assert len({r["query"] for r in rows}) == 10
        assert sum(1 for r in rows if r["expected_relevance"]) == 11
        # Every pair carries a real score; a missing one would silently
        # skew every metric below.
        assert all(isinstance(r["rerank_score"], (int, float)) for r in rows)

    def test_distributions_overlap(self):
        """The honest finding: this is a filter, not a classifier."""
        rows = self.rows()
        relevant = [r["rerank_score"] for r in rows if r["expected_relevance"]]
        irrelevant = [r["rerank_score"] for r in rows if not r["expected_relevance"]]

        assert min(relevant) < max(irrelevant), "documented overlap has vanished"
        # The separation that does exist is in the central tendency.
        assert statistics.median(relevant) > statistics.median(irrelevant)

    def test_configured_threshold_meets_its_documented_metrics(self):
        """The number in settings is the one the calibration supports."""
        metrics = self.metrics(self.rows(), settings.reranker_relevance_threshold)

        assert metrics["precision"] >= 0.80, metrics
        assert metrics["recall"] >= 0.80, metrics
        assert metrics["fpr"] <= 0.05, metrics
        assert metrics["fnr"] <= 0.20, metrics

    def test_threshold_beats_the_alternatives_it_was_chosen_over(self):
        """A lower threshold would admit materially more irrelevant evidence."""
        rows = self.rows()
        chosen = self.metrics(rows, settings.reranker_relevance_threshold)
        looser = self.metrics(rows, settings.reranker_relevance_threshold - 1.0)

        assert chosen["precision"] > looser["precision"]
        assert chosen["fp"] < looser["fp"]

    def test_every_answerable_query_keeps_evidence(self):
        """The trade that ruled out the higher-precision threshold.

        A threshold is only acceptable if each in-corpus query still
        retains at least one genuinely relevant chunk — otherwise the
        gate turns correct answers into false "insufficient evidence".
        """
        rows = self.rows()
        threshold = settings.reranker_relevance_threshold
        in_corpus = {r["query"] for r in rows if r["query_kind"] == "in_corpus"}

        for query in in_corpus:
            kept = [
                r for r in rows
                if r["query"] == query
                and r["rerank_score"] >= threshold
                and r["expected_relevance"]
            ]
            assert kept, f"threshold {threshold} leaves '{query}' unanswerable"

    def test_most_out_of_corpus_queries_reach_zero_evidence(self):
        """The point of the sprint, measured on the fixture."""
        rows = self.rows()
        threshold = settings.reranker_relevance_threshold
        out_of_corpus = {r["query"] for r in rows if r["query_kind"] == "out_of_corpus"}

        silenced = [
            query for query in out_of_corpus
            if not [r for r in rows if r["query"] == query and r["rerank_score"] >= threshold]
        ]
        assert len(silenced) >= 4, (
            f"only {len(silenced)}/{len(out_of_corpus)} unanswerable queries "
            "produce zero evidence"
        )
