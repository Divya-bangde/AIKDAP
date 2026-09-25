"""Sprint 9G/9H Phases 17-18: a fallback answer is held to the same standard.

The tempting shortcut, when the primary model is down, is to relax
something: send a simpler prompt to a weaker model, or trust its
citations because at least it answered. That would make an outage
silently degrade the *truthfulness* of the product, not just its
latency — and nothing in the run record would say so.

So the rule is: only the generation provider changes. Same evidence,
same prompt, same citation validation, same relevance gate. These tests
exist to make that rule expensive to break.

They reuse `test_grounded_synthesis.py`'s document builders rather than
duplicating them, so evidence here is shaped exactly as the real
Sprint 9C/9D pipeline emits it.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.agents.planner.synthesis import GroundedSynthesizer
from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.provider_health import ProviderHealthRegistry
from app.modules.research.enums import ResearchGroundingStatus
from tests.test_grounded_synthesis import asset_document, citations_from

GEMINI = "gemini/gemini-flash-latest"
GROQ = "groq/openai/gpt-oss-120b"

FAKE_GEMINI_KEY = "AIzaSyTESTKEY_MUST_NEVER_BE_LOGGED_0123456789"
FAKE_GROQ_KEY = "gsk_TESTKEYMUSTNEVERBELOGGED0123456789"


def quota_exhausted() -> Exception:
    """Gemini's real daily-quota refusal, abbreviated to its markers."""
    error = Exception(
        "litellm.RateLimitError: geminiException - 429 RESOURCE_EXHAUSTED "
        '"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"'
    )
    error.status_code = 429  # type: ignore[attr-defined]
    return error


def reply(*, answer: str, citation_ids: list[str], model: str) -> SimpleNamespace:
    """A synthesis JSON payload in LiteLLM's response shape."""
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "answer": answer,
                            "citation_ids": citation_ids,
                            "grounding_status": "grounded",
                        }
                    )
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 128}),
    )


EVIDENCE = [
    asset_document(1, "ABC Poultry produced 1.2 million tonnes of feed.", rerank_score=9.98),
    asset_document(2, "Feed cost inflation was the principal challenge.", rerank_score=4.10),
]


@pytest.fixture
def both_providers(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_GEMINI_KEY))
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))
    monkeypatch.setattr(settings, "openrouter_api_key", None)


@pytest.fixture
def call(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    return mock


def synthesizer() -> GroundedSynthesizer:
    """A real synthesizer over a real gateway with a Groq fallback."""
    from app.core.llm.gateway import LLMGateway

    return GroundedSynthesizer(
        gateway=LLMGateway(
            default_model=GEMINI,
            fallback_model=GROQ,
            secondary_fallback_model="",
            provider_health=ProviderHealthRegistry(),
        ),
        model=GEMINI,
    )


async def synthesize(citations):
    return await synthesizer().synthesize(
        query="What challenges does ABC Poultry face?",
        objective="Summarize the challenges.",
        context="",
        documents=[],
        citations=citations,
        warnings=[],
    )


def prompt_of(call: AsyncMock, index: int) -> str:
    """The full text sent on the call at `index`."""
    request = call.await_args_list[index].kwargs
    return "\n".join(message["content"] for message in request["messages"])


# ---------------------------------------------------------------------------
# TEST 13 — the fallback receives the same evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fallback_receives_byte_identical_evidence(both_providers, call):
    """Not "similar" evidence. The same bytes.

    A weaker fallback model is not a reason to send it a shorter or
    softer prompt: the grounding guarantees are enforced against what
    was sent, so a different prompt would mean different guarantees.
    """
    call.side_effect = [
        quota_exhausted(),
        reply(answer="Feed cost inflation [c2].", citation_ids=["c2"], model=GROQ),
    ]

    await synthesize(citations_from(EVIDENCE))

    assert call.await_count == 2
    assert prompt_of(call, 0) == prompt_of(call, 1)
    assert call.await_args_list[0].kwargs["model"] == GEMINI
    assert call.await_args_list[1].kwargs["model"] == GROQ


@pytest.mark.asyncio
async def test_the_fallback_gets_the_same_structured_output_contract(
    both_providers, call
):
    """JSON mode is part of the contract, not a Gemini nicety.

    A fallback that cannot return the envelope cannot be validated, so
    the configured fallback was chosen by sending it the real synthesis
    prompt and checking the parsed result — not by asking it to say
    "OK". `response_format` must reach every provider in the chain
    identically for that guarantee to hold.
    """
    call.side_effect = [
        quota_exhausted(),
        reply(answer="Feed cost inflation [c2].", citation_ids=["c2"], model=GROQ),
    ]

    await synthesize(citations_from(EVIDENCE))

    formats = [request.kwargs["response_format"] for request in call.await_args_list]
    assert formats[0]["type"] == "json_schema"
    assert all(fmt == formats[0] for fmt in formats)


@pytest.mark.asyncio
async def test_the_fallback_cannot_see_evidence_the_primary_could_not(
    both_providers, call
):
    """Withheld evidence stays withheld, whoever answers.

    Simulated placeholders are excluded from the primary's prompt
    because they contain no facts. That reasoning does not change when
    a different model is asking.
    """
    from tests.test_grounded_synthesis import simulated_document

    call.side_effect = [
        quota_exhausted(),
        reply(answer="Feed cost inflation [c1].", citation_ids=["c1"], model=GROQ),
    ]

    await synthesize(citations_from([EVIDENCE[1], simulated_document(2)]))

    assert "SIMULATED RESULT" not in prompt_of(call, 1)


# ---------------------------------------------------------------------------
# TEST 14 — citations from a fallback are validated identically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fallback_cannot_invent_citation_ids(both_providers, call):
    """FINAL_CITATIONS ⊆ EVIDENCE_SENT holds for every provider.

    No fuzzy matching, no repair. An id that was not supplied is
    dropped, and the answer is downgraded to `partially_grounded`.
    """
    call.side_effect = [
        quota_exhausted(),
        reply(
            answer="Feed cost inflation [c2], and a made-up claim [c99].",
            citation_ids=["c2", "c99", "fake-id"],
            model=GROQ,
        ),
    ]

    result = await synthesize(citations_from(EVIDENCE))

    assert [citation["id"] for citation in result.citations] == ["c2"]
    assert result.rejected_citation_ids == ["c99", "fake-id"]
    assert result.grounding_status is ResearchGroundingStatus.PARTIALLY_GROUNDED


@pytest.mark.asyncio
async def test_a_fallback_citing_nothing_valid_is_not_grounded(both_providers, call):
    """A model that answered is not the same as an answer that is supported."""
    call.side_effect = [
        quota_exhausted(),
        reply(answer="Confident nonsense [c42].", citation_ids=["c42"], model=GROQ),
    ]

    result = await synthesize(citations_from(EVIDENCE))

    assert result.citations == []
    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_a_fallback_answer_is_labelled_as_one(both_providers, call):
    """Phase 16: the trace must show which provider actually answered."""
    call.side_effect = [
        quota_exhausted(),
        reply(answer="Feed cost inflation [c2].", citation_ids=["c2"], model=GROQ),
    ]

    result = await synthesize(citations_from(EVIDENCE))

    assert result.fallback_used is True
    assert result.provider == "groq"
    assert result.primary_model == GEMINI
    assert result.primary_error_type == "LLMQuotaExhaustedError"
    assert result.llm_attempts == 2


@pytest.mark.asyncio
async def test_a_primary_answer_is_not_labelled_as_a_fallback(both_providers, call):
    """The inverse, so the flag means something."""
    call.side_effect = [
        reply(answer="Feed cost inflation [c2].", citation_ids=["c2"], model=GEMINI)
    ]

    result = await synthesize(citations_from(EVIDENCE))

    assert result.fallback_used is False
    assert result.primary_error_type is None


# ---------------------------------------------------------------------------
# TEST 15 — the relevance gate is upstream of every provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_accepted_evidence_calls_no_provider_at_all(both_providers, call):
    """Sprint 9F's guarantee, extended across the chain.

    With nothing to ground in, there is no provider worth asking — not
    Gemini, not Groq, not OpenRouter. A fallback chain must not become a
    reason to start calling models for questions the evidence cannot
    answer.
    """
    result = await synthesize([])

    call.assert_not_awaited()
    assert result.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    assert result.citations == []
    assert result.model is None
    assert result.provider is None


@pytest.mark.asyncio
async def test_evidence_rejected_by_the_gate_never_reaches_the_fallback(
    both_providers, call
):
    """The gate runs during retrieval, so rejected chunks are simply
    not in the citation list any synthesizer receives.

    Asserted through the fallback path specifically: a provider swap
    happens after evidence selection, and must not reopen it.
    """
    call.side_effect = [
        quota_exhausted(),
        reply(answer="Feed cost inflation [c1].", citation_ids=["c1"], model=GROQ),
    ]
    # Only the accepted chunk is passed, exactly as `_rerank_hits`
    # would hand it over after the gate.
    accepted = citations_from([EVIDENCE[1]])

    await synthesize(accepted)

    fallback_prompt = prompt_of(call, 1)
    assert "Feed cost inflation" in fallback_prompt
    assert "1.2 million tonnes" not in fallback_prompt


# ---------------------------------------------------------------------------
# Sprint 9H — OpenRouter as the third link (Phases 17, 20, 21, 32)
# ---------------------------------------------------------------------------

OPENROUTER = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
FAKE_OPENROUTER_KEY = "sk-or-v1-testkeymustneverbelogged0123456789"


@pytest.fixture
def all_three_providers(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_GEMINI_KEY))
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))
    monkeypatch.setattr(settings, "openrouter_api_key", SecretStr(FAKE_OPENROUTER_KEY))


def groq_unavailable() -> Exception:
    error = Exception("litellm.InternalServerError: GroqException - 503 overloaded")
    error.status_code = 503  # type: ignore[attr-defined]
    return error


def synthesizer_with_openrouter() -> GroundedSynthesizer:
    """Gemini -> Groq -> OpenRouter, the real deployed chain shape."""
    from app.core.llm.gateway import LLMGateway

    return GroundedSynthesizer(
        gateway=LLMGateway(
            default_model=GEMINI,
            fallback_model=GROQ,
            secondary_fallback_model=OPENROUTER,
            provider_health=ProviderHealthRegistry(),
        ),
        model=GEMINI,
    )


async def synthesize_with_openrouter(citations):
    return await synthesizer_with_openrouter().synthesize(
        query="What challenges does ABC Poultry face?",
        objective="Summarize the challenges.",
        context="",
        documents=[],
        citations=citations,
        warnings=[],
    )


@pytest.mark.asyncio
async def test_openrouter_answers_when_gemini_and_groq_both_fail(
    all_three_providers, call
):
    """Phase 17 / Scenario D end-to-end through synthesis, not just the
    gateway: Gemini's quota is spent, Groq is down, OpenRouter carries
    the run — and the answer is still grounded and cited."""
    call.side_effect = [
        quota_exhausted(),
        groq_unavailable(),
        groq_unavailable(),
        groq_unavailable(),
        reply(
            answer="Feed cost inflation compressed margins [c1].",
            citation_ids=["c1"],
            model=OPENROUTER,
        ),
    ]

    result = await synthesize_with_openrouter(citations_from(EVIDENCE))

    assert [c.kwargs["model"] for c in call.await_args_list] == [
        GEMINI, GROQ, GROQ, GROQ, OPENROUTER,
    ]
    assert result.provider == "openrouter"
    assert result.fallback_used is True
    assert result.primary_model == GEMINI
    assert [citation["id"] for citation in result.citations] == ["c1"]
    assert result.grounding_status is ResearchGroundingStatus.GROUNDED


@pytest.mark.asyncio
async def test_openrouter_receives_the_same_evidence_as_gemini(
    all_three_providers, call
):
    """Phase 20: the third link gets no weaker a contract than the
    first — same evidence, byte for byte.

    Groq is retried up to `LLM_MAX_RETRIES + 1 = 3` times before the
    chain moves on, so it needs three failures here, not one — the
    same count `test_openrouter_answers_when_gemini_and_groq_both_fail`
    already gets right; this test got it wrong on a first pass and the
    fix is recorded so it doesn't regress silently a second time.
    """
    call.side_effect = [
        quota_exhausted(),
        groq_unavailable(),
        groq_unavailable(),
        groq_unavailable(),
        reply(answer="Feed cost inflation [c2].", citation_ids=["c2"], model=OPENROUTER),
    ]

    await synthesize_with_openrouter(citations_from(EVIDENCE))

    assert prompt_of(call, 0) == prompt_of(call, 4)
    assert call.await_args_list[4].kwargs["model"] == OPENROUTER
    assert call.await_args_list[4].kwargs["response_format"] == call.await_args_list[0].kwargs["response_format"]


@pytest.mark.asyncio
async def test_openrouter_citation_attack_is_rejected(all_three_providers, call):
    """Phase 21/32: a controlled OpenRouter response citing a real id,
    a plausible-looking fake, and one that was never supplied at all.

    Only the real id survives. No fuzzy matching brings the others
    back, and the run is downgraded rather than silently accepted as
    fully grounded.
    """
    call.side_effect = [
        quota_exhausted(),
        groq_unavailable(),
        groq_unavailable(),
        groq_unavailable(),
        reply(
            answer="Feed cost inflation [c1], plus fabricated points [fake-id][c999].",
            citation_ids=["c1", "fake-id", "c999"],
            model=OPENROUTER,
        ),
    ]

    result = await synthesize_with_openrouter(citations_from(EVIDENCE))

    assert [citation["id"] for citation in result.citations] == ["c1"]
    assert set(result.rejected_citation_ids) == {"fake-id", "c999"}
    assert result.grounding_status is ResearchGroundingStatus.PARTIALLY_GROUNDED
    # The rejected ids are named for the reader, never silently dropped.
    assert "fake-id" in result.answer
    assert "c999" in result.answer


@pytest.mark.asyncio
async def test_openrouter_cannot_cite_evidence_rejected_by_the_gate(
    all_three_providers, call
):
    """Phase 20/21 combined: the relevance gate's decision holds even
    for the provider furthest down the chain."""
    call.side_effect = [
        quota_exhausted(),
        groq_unavailable(),
        groq_unavailable(),
        groq_unavailable(),
        reply(answer="Feed cost inflation [c1].", citation_ids=["c1"], model=OPENROUTER),
    ]
    accepted = citations_from([EVIDENCE[1]])  # only the gate-accepted chunk

    await synthesize_with_openrouter(accepted)

    openrouter_prompt = prompt_of(call, 4)
    assert "Feed cost inflation" in openrouter_prompt
    assert "1.2 million tonnes" not in openrouter_prompt
