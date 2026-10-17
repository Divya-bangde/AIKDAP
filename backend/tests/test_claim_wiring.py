"""Sprint 16 Phase 8.7 -- claims come from the synthesis schema, not a
second extractor.

`GroundedSynthesizer` already asks the model for structured JSON; these
tests prove the extended schema (`claims`) is parsed, bound to the
evidence actually supplied, and run through the real Phase 8.5
deterministic verifier -- and that none of this can break an answer
that would otherwise have worked (claims are untrusted, additive
output).
"""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.agents.planner.nodes import GraphDependencies, synthesis_node
from app.agents.planner.planner import get_planner
from app.agents.planner.synthesis import GroundedSynthesizer
from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.gateway import LLMGateway
from app.modules.research.schemas import ResearchRunDetail, VerifiedClaimRead

from tests.test_grounded_synthesis import (
    SYNTHESIS_MODEL,
    asset_document,
    citations_from,
    model_reply,
    synthesize,
)


def model_reply_with_claims(*, claims: list[dict], citation_ids: list[str] | None = None) -> SimpleNamespace:
    content = json.dumps(
        {
            "answer": "The overall rate was 73.5% [c1].",
            "citation_ids": ["c1"] if citation_ids is None else citation_ids,
            "grounding_status": "grounded",
            "claims": claims,
        }
    )
    return SimpleNamespace(
        model=SYNTHESIS_MODEL,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        ),
    )


@pytest.fixture
def litellm_call(monkeypatch) -> AsyncMock:
    mock = AsyncMock(return_value=model_reply())
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("offline-gemini-fixture"))
    return mock


@pytest.fixture
def synthesizer() -> GroundedSynthesizer:
    return GroundedSynthesizer(gateway=LLMGateway(), model=SYNTHESIS_MODEL)


@pytest.mark.asyncio
async def test_contradicted_aggregate_claim_is_verified_as_such(synthesizer, litellm_call):
    """The real Phase 8.4 failure mode: a component-scoped number claimed as an aggregate."""
    documents = [
        asset_document(0, "S4: One-Shot + BLUF 200 3.55 3.35 3.70 4.07 3.60 / 73.5% Figure 1."),
    ]
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": "The overall rate was 73.5%.",
                "claim_type": "numeric",
                "claimed_value": "73.5%",
                "scope": "aggregate",
                "source_reference_ids": ["c1"],
                "attributed_to_primary": True,
            }
        ]
    )

    result = await synthesize(synthesizer, documents)

    assert len(result.verified_claims) == 1
    claim = result.verified_claims[0]
    assert claim["kind"] == "claim"
    assert claim["verdict"] == "contradicted"
    assert claim["evidence_state"] == "contradicted"
    assert claim["source_reference_ids"] == ["c1"]
    assert claim["unresolved_citation_ids"] == []


@pytest.mark.asyncio
async def test_supported_categorical_claim_is_verified(synthesizer, litellm_call):
    documents = [asset_document(0, "Allowlist suppression raised detection from two to five families.")]
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": "Allowlist suppression raised detection from two to five families.",
                "claim_type": "categorical",
                "source_reference_ids": ["c1"],
            }
        ]
    )

    result = await synthesize(synthesizer, documents)

    assert result.verified_claims[0]["verdict"] == "supported"
    assert result.verified_claims[0]["evidence_state"] == "verified"


# ---------------------------------------------------------------------------
# Sprint 16 Phase 8.10 blocker: verification was checking `snippet`
# (truncated to `SNIPPET_CHARACTERS`, 700) instead of the chunk's full
# content (up to 1000 characters) -- a claim citing text past that
# cutoff was being checked against text the model was shown but the
# verifier never saw. `full_evidence_text` (chunk_id -> full content) is
# now threaded through `synthesize()` -> `_verify_claims`, resolved in
# `nodes._resolve_full_evidence_text` from a real repository. `snippet`
# itself is untouched -- these tests assert that explicitly too.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_claims_reads_full_chunk_content_not_truncated_snippet(synthesizer, litellm_call):
    """A claim citing a phrase past the snippet's cutoff is UNVERIFIABLE
    without `full_evidence_text` and SUPPORTED with it -- the same
    citation, same claim, only the evidence text resolution differs.
    """
    truncated_snippet = "JA4 fingerprints achieved 100% detection according to preliminary review only"
    full_content = (
        "JA4 fingerprints achieved 100% detection according to preliminary review "
        "only; a fuller analysis confirmed the same 100% detection rate across "
        "the entire evaluated corpus of network flows, confirming strong reliability."
    )
    documents = [asset_document(0, truncated_snippet)]
    claim_text = "JA4 fingerprints achieved 100% detection across the entire evaluated corpus of network flows."
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": claim_text,
                "claim_type": "categorical",
                "source_reference_ids": ["c1"],
            }
        ]
    )
    citations = citations_from(documents)
    chunk_id = citations[0]["chunk_id"]

    without_full_text = await synthesizer.synthesize(
        query="q",
        objective="o",
        context="c",
        documents=documents,
        citations=citations,
        warnings=[],
    )
    assert without_full_text.verified_claims[0]["verdict"] == "unverifiable"
    # `snippet` itself must never be rewritten -- only the text handed to
    # the verifier changes.
    assert without_full_text.citations[0]["snippet"] == truncated_snippet

    with_full_text = await synthesizer.synthesize(
        query="q",
        objective="o",
        context="c",
        documents=documents,
        citations=citations,
        warnings=[],
        full_evidence_text={chunk_id: full_content},
    )
    assert with_full_text.verified_claims[0]["verdict"] == "supported"
    assert with_full_text.citations[0]["snippet"] == truncated_snippet


@pytest.mark.asyncio
async def test_synthesis_node_resolves_full_content_via_chunk_repository(synthesizer, litellm_call):
    """End-to-end through `synthesis_node`: a fake repository stands in
    for the database (matching `GraphDependencies`'s own documented
    reason for being a dependency-injection point), proving the node
    wiring -- not just `synthesize()` in isolation -- reaches the fix.
    """
    truncated_snippet = "the second phase introduced the allow"
    full_content = "3.4.2 Phase 2 - the second phase introduced the allowlist, which removes known-benign flows."
    documents = [asset_document(0, truncated_snippet)]
    claim_text = "The second phase introduced the allowlist, which removes known-benign flows."
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": claim_text,
                "claim_type": "categorical",
                "source_reference_ids": ["c1"],
            }
        ]
    )
    citations = citations_from(documents)
    chunk_id = citations[0]["chunk_id"]

    class FakeChunkRepository:
        async def get_by_id(self, requested_id):
            if str(requested_id) == chunk_id:
                return SimpleNamespace(content=full_content, asset_id=citations[0]["asset_id"], project_id="test-project")
            return None

    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=None,
        web_provider=None,
        synthesizer=synthesizer,
        llm_gateway=LLMGateway(),
        chunk_repository=FakeChunkRepository(),
    )
    state = {
        "run_id": str(uuid.uuid4()),
        "project_id": "test-project",
        "query": "What did the second phase introduce?",
        "objective": "Answer from the knowledge base.",
        "context": "built by the context builder",
        "retrieved_documents": documents,
        "citations": citations,
    }

    update = await synthesis_node(state, {"configurable": {"dependencies": dependencies}})

    claim_entries = [item for item in update["citations"] if item.get("kind") == "claim"]
    assert claim_entries[0]["verdict"] == "supported"


@pytest.mark.asyncio
async def test_synthesis_node_without_chunk_repository_falls_back_to_snippet(synthesizer, litellm_call):
    """`chunk_repository=None` (existing fakes/tests that never set one)
    must behave exactly as before this phase -- no crash, snippet used.
    """
    documents = [asset_document(0, "the second phase introduced the allow")]
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": "The second phase introduced the allowlist, which removes known-benign flows.",
                "claim_type": "categorical",
                "source_reference_ids": ["c1"],
            }
        ]
    )
    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=None,
        web_provider=None,
        synthesizer=synthesizer,
        llm_gateway=LLMGateway(),
    )
    state = {
        "run_id": str(uuid.uuid4()),
        "query": "What did the second phase introduce?",
        "objective": "Answer from the knowledge base.",
        "context": "built by the context builder",
        "retrieved_documents": documents,
        "citations": citations_from(documents),
    }

    update = await synthesis_node(state, {"configurable": {"dependencies": dependencies}})

    claim_entries = [item for item in update["citations"] if item.get("kind") == "claim"]
    assert claim_entries[0]["verdict"] == "unverifiable"


@pytest.mark.asyncio
async def test_claim_citing_an_invented_id_is_untrusted(synthesizer, litellm_call):
    """Schema-valid is not the same as supported: an invented id resolves to no evidence."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": "ABC Poultry produced 1.2 million tonnes.",
                "claim_type": "categorical",
                "source_reference_ids": ["c1", "fake-id"],
            }
        ]
    )

    result = await synthesize(synthesizer, documents)

    claim = result.verified_claims[0]
    assert claim["unresolved_citation_ids"] == ["fake-id"]
    # The real id still resolves and still verifies -- one invented id
    # alongside a real one does not poison the whole claim.
    assert claim["source_reference_ids"] == ["c1"]
    assert claim["verdict"] == "supported"


@pytest.mark.asyncio
async def test_claim_with_no_valid_citations_is_insufficient_evidence(synthesizer, litellm_call):
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": "Something else entirely.",
                "claim_type": "categorical",
                "source_reference_ids": ["fake-id"],
            }
        ]
    )

    result = await synthesize(synthesizer, documents)

    claim = result.verified_claims[0]
    assert claim["verdict"] == "insufficient_evidence"
    assert claim["evidence_state"] == "unknown"


@pytest.mark.asyncio
async def test_malformed_claim_entry_is_dropped_not_fatal(synthesizer, litellm_call):
    """One bad claim must never take down an otherwise-valid answer."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]
    content = json.dumps(
        {
            "answer": "ABC Poultry produced 1.2 million tonnes [c1].",
            "citation_ids": ["c1"],
            "grounding_status": "grounded",
            "claims": [{"claim_text": "missing claim_type entirely"}],
        }
    )
    litellm_call.return_value = SimpleNamespace(
        model=SYNTHESIS_MODEL,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        ),
    )

    result = await synthesize(synthesizer, documents)

    assert result.answer  # the answer itself is unaffected
    assert result.verified_claims == []


@pytest.mark.asyncio
async def test_no_claims_in_response_is_fine(synthesizer, litellm_call):
    """Claims are additive: an envelope with none must behave exactly as before."""
    documents = [asset_document(0, "ABC Poultry produced 1.2 million tonnes.")]

    result = await synthesize(synthesizer, documents)  # default model_reply(), no claims key

    assert result.verified_claims == []


@pytest.mark.asyncio
async def test_synthesis_node_stores_claims_alongside_citations(synthesizer, litellm_call):
    """Claims ride in the same `citations` state list, tagged, real citations first."""
    documents = [
        asset_document(0, "S4: One-Shot + BLUF 200 3.55 3.35 3.70 4.07 3.60 / 73.5% Figure 1.")
    ]
    litellm_call.return_value = model_reply_with_claims(
        claims=[
            {
                "claim_text": "The overall rate was 73.5%.",
                "claim_type": "numeric",
                "claimed_value": "73.5%",
                "scope": "aggregate",
                "source_reference_ids": ["c1"],
            }
        ]
    )

    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=None,
        web_provider=None,
        synthesizer=synthesizer,
        llm_gateway=LLMGateway(),
    )
    state = {
        "run_id": str(uuid.uuid4()),
        "query": "What was the rate?",
        "objective": "Answer from the knowledge base.",
        "context": "built by the context builder",
        "retrieved_documents": documents,
        "citations": citations_from(documents),
    }

    update = await synthesis_node(state, {"configurable": {"dependencies": dependencies}})

    stored = update["citations"]
    assert stored[0]["chunk_id"]  # real citation, unaffected, still first
    assert "kind" not in stored[0]
    claim_entries = [item for item in stored if item.get("kind") == "claim"]
    assert len(claim_entries) == 1
    assert claim_entries[0]["verdict"] == "contradicted"
    # The step trace carries claims too, for the Explainable-AI record.
    assert update["step"]["output"]["claims"] == claim_entries


@pytest.mark.asyncio
async def test_claim_evidence_is_exposed_even_when_top_level_cites_nothing(
    synthesizer, litellm_call
):
    """Sprint 16 Phase 8.8 Part B. Real: run 8127bd38-6b2a-47ee-8560-7835fc8c0087
    (vaniscak_c2, Phase 8.7 production) -- the top-level answer was
    `insufficient_evidence` with zero cited ids, but its one claim still
    resolved real, supplied evidence (`c1`). Before this fix, `c1` never
    appeared in `run.citations`, leaving the panel's evidence chip
    disabled with no way to distinguish "invented id" from "valid id the
    API just didn't return".
    """
    documents = [asset_document(0, "JA4 fingerprints achieved 100% detection in this study.")]
    content = json.dumps(
        {
            "answer": "The provided evidence does not contain enough information to answer the question.",
            "citation_ids": [],
            "grounding_status": "insufficient_evidence",
            "claims": [
                {
                    "claim_text": "JA4 fingerprints achieved 100% detection.",
                    "claim_type": "categorical",
                    "source_reference_ids": ["c1"],
                }
            ],
        }
    )
    litellm_call.return_value = SimpleNamespace(
        model=SYNTHESIS_MODEL,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        ),
    )

    dependencies = GraphDependencies(
        planner=get_planner(),
        asset_retriever=None,
        web_provider=None,
        synthesizer=synthesizer,
        llm_gateway=LLMGateway(),
    )
    state = {
        "run_id": str(uuid.uuid4()),
        "query": "What was the detection rate?",
        "objective": "Answer from the knowledge base.",
        "context": "built by the context builder",
        "retrieved_documents": documents,
        "citations": citations_from(documents),
    }

    update = await synthesis_node(state, {"configurable": {"dependencies": dependencies}})

    stored = update["citations"]
    claim_entries = [item for item in stored if item.get("kind") == "claim"]
    plain_citations = [item for item in stored if item.get("kind") != "claim"]

    assert claim_entries[0]["verdict"] == "supported"
    assert claim_entries[0]["source_reference_ids"] == ["c1"]
    # The real regression: the top-level answer cited nothing at all,
    # but the claim's evidence must still be retrievable.
    assert any(c["id"] == "c1" for c in plain_citations)
    resolved = next(c for c in plain_citations if c["id"] == "c1")
    assert resolved["snippet"] == documents[0]["snippet"]  # copied verbatim, not invented


def test_research_run_detail_exposes_claim_evidence_absent_from_top_level_citations():
    """The read-model split (`ResearchRunDetail.from_model`) must carry
    the unioned evidence through -- a claim referencing 'c1' must find
    a real 'c1' citation object among `detail.citations`.
    """
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        task_id=None,
        query="q",
        status="completed",
        include_assets=True,
        include_web=True,
        max_results=5,
        objective="o",
        plan=None,
        final_answer="answer",
        citations=[
            # No plain citation was ever top-level-cited for this run --
            # only the union added by `synthesis_node` puts c1 here.
            {"id": "c1", "title": "Real citation", "snippet": "real evidence text", "simulated": False},
            {
                "kind": "claim",
                "claim_text": "JA4 fingerprints achieved 100% detection.",
                "claim_type": "categorical",
                "claimed_value": None,
                "scope": None,
                "source_reference_ids": ["c1"],
                "unresolved_citation_ids": [],
                "attributed_to_primary": True,
                "verdict": "supported",
                "evidence_state": "verified",
                "matched_evidence_ids": ["c1"],
                "reason": "claim text matches cited evidence",
            },
        ],
        grounding_status="insufficient_evidence",
        error_message=None,
        celery_task_id=None,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    detail = ResearchRunDetail.from_model(run, steps=[], messages=[])

    assert len(detail.claims) == 1
    referenced_id = detail.claims[0].source_reference_ids[0]
    assert any(c["id"] == referenced_id for c in detail.citations)


def test_research_run_detail_splits_claims_out_of_citations():
    """The one read-time seam: `citations` JSONB -> (citations, claims)."""
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        task_id=None,
        query="q",
        status="completed",
        include_assets=True,
        include_web=True,
        max_results=5,
        objective="o",
        plan=None,
        final_answer="answer",
        citations=[
            {"id": "c1", "title": "Real citation", "snippet": "s", "simulated": False},
            {
                "kind": "claim",
                "claim_text": "The overall rate was 73.5%.",
                "claim_type": "numeric",
                "claimed_value": "73.5%",
                "scope": "aggregate",
                "source_reference_ids": ["c1"],
                "unresolved_citation_ids": [],
                "attributed_to_primary": True,
                "verdict": "contradicted",
                "evidence_state": "contradicted",
                "matched_evidence_ids": ["c1"],
                "reason": "scoped to a named component",
            },
        ],
        grounding_status="grounded",
        error_message=None,
        celery_task_id=None,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    detail = ResearchRunDetail.from_model(run, steps=[], messages=[])

    assert len(detail.citations) == 1
    assert detail.citations[0]["id"] == "c1"
    assert len(detail.claims) == 1
    assert isinstance(detail.claims[0], VerifiedClaimRead)
    assert detail.claims[0].verdict == "contradicted"
