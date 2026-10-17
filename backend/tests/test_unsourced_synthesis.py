"""Sprint 16 Phase 8.13: the opt-in "answer from general knowledge" path.

Same offline convention as `test_grounded_synthesis.py`: only LiteLLM's
entry point is replaced, so every call still travels the real
`LLMGateway`. The load-bearing assertions here are the negative ones --
that no evidence ever reaches this prompt, and that the resulting
citations array is empty by construction rather than by a check that
could itself have a bug in it.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.agents.planner.synthesis import (
    SynthesisResponseError,
    UnsourcedSynthesizer,
    get_synthesizer,
)
from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.gateway import LLMGateway, LLMProviderError
from app.modules.research.enums import ResearchGroundingStatus

SYNTHESIS_MODEL = "ollama/qwen3.5:4b"


def model_reply(
    *,
    answer: str = "General knowledge answer about the topic.",
    would_need: str | None = "a peer-reviewed measurement of this exact metric",
    raw: str | None = None,
) -> SimpleNamespace:
    """Build a LiteLLM-shaped response carrying an unsourced synthesis payload."""
    content = raw
    if content is None:
        payload: dict = {"answer": answer}
        if would_need is not None:
            payload["would_need"] = would_need
        content = json.dumps(payload)
    return SimpleNamespace(
        model=SYNTHESIS_MODEL,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70}
        ),
    )


@pytest.fixture
def litellm_call(monkeypatch) -> AsyncMock:
    """Replace LiteLLM's entry point, keeping the real gateway in the path."""
    mock = AsyncMock(return_value=model_reply())
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("offline-gemini-fixture"))
    return mock


@pytest.fixture
def synthesizer() -> UnsourcedSynthesizer:
    return UnsourcedSynthesizer(gateway=LLMGateway(), model=SYNTHESIS_MODEL)


def sent_messages(litellm_call) -> list[dict]:
    return litellm_call.await_args.kwargs["messages"]


def sent_text(litellm_call) -> str:
    return "\n".join(message["content"] for message in sent_messages(litellm_call))


# ---------------------------------------------------------------------------
# No evidence is ever sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_evidence_reaches_the_prompt(synthesizer, litellm_call):
    """Only the question travels in this prompt -- nothing evidence-shaped."""
    await synthesizer.synthesize(query="What is the typical latency of this algorithm?")

    messages = sent_messages(litellm_call)
    assert [message["role"] for message in messages] == ["system", "user"]
    user_message = messages[1]["content"]
    assert "What is the typical latency of this algorithm?" in user_message
    # The user message -- the actual request, as opposed to the system
    # prompt's own example of a forbidden marker -- carries no evidence
    # or citation shape at all.
    assert "[c1]" not in user_message
    assert "chunk" not in user_message.lower()


@pytest.mark.asyncio
async def test_system_prompt_forbids_inventing_citations_or_the_paper(litellm_call):
    """The system prompt itself must carry the anti-fabrication rules."""
    from app.agents.planner.prompts import UNSOURCED_SYNTHESIS_SYSTEM_PROMPT

    assert "never invent" in UNSOURCED_SYNTHESIS_SYSTEM_PROMPT.lower()
    assert '"the paper"' in UNSOURCED_SYNTHESIS_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Citations are empty by construction, not by check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citations_are_always_empty_even_if_the_model_tries_to_cite(
    synthesizer, litellm_call
):
    """A model that emits `citation_ids` anyway must still yield zero citations.

    `UnsourcedSynthesisResponse` has no `citation_ids` field, so
    `_parse_unsourced_response` never looks for one -- this proves the
    guarantee holds even when the raw model output contains the key,
    not merely when it is absent.
    """
    litellm_call.return_value = model_reply(
        raw=json.dumps(
            {
                "answer": "The answer [c1].",
                "would_need": "a real citable source",
                "citation_ids": ["c1", "fabricated-id"],
            }
        )
    )

    result = await synthesizer.synthesize(query="Anything")

    assert result.citations == []
    assert result.grounding_status is ResearchGroundingStatus.UNSOURCED


@pytest.mark.asyncio
async def test_grounding_status_is_unsourced(synthesizer, litellm_call):
    result = await synthesizer.synthesize(query="Anything")
    assert result.grounding_status is ResearchGroundingStatus.UNSOURCED
    assert result.evidence_supplied == 0


# ---------------------------------------------------------------------------
# Answer framing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_is_framed_as_a_research_lead_not_an_answer(synthesizer, litellm_call):
    litellm_call.return_value = model_reply(
        answer="The typical latency is around 40ms.",
        would_need="a benchmark measuring this exact configuration.",
    )

    result = await synthesizer.synthesize(query="What is the typical latency?")

    assert "Not in your uploaded papers" in result.answer
    assert "From general knowledge" in result.answer
    assert "The typical latency is around 40ms." in result.answer
    assert "To make this citable" in result.answer
    assert "a benchmark measuring this exact configuration." in result.answer


@pytest.mark.asyncio
async def test_missing_would_need_degrades_gracefully(synthesizer, litellm_call):
    """A schema-valid-but-incomplete reply must not fail the whole answer."""
    litellm_call.return_value = model_reply(would_need=None)

    result = await synthesizer.synthesize(query="Anything")

    assert "To make this citable" in result.answer
    assert result.grounding_status is ResearchGroundingStatus.UNSOURCED


# ---------------------------------------------------------------------------
# Failure paths mirror the grounded synthesizer's
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_failure_produces_no_answer(synthesizer, litellm_call):
    litellm_call.side_effect = RuntimeError("ollama exploded")

    with pytest.raises(LLMProviderError):
        await synthesizer.synthesize(query="Anything")


@pytest.mark.asyncio
async def test_unparseable_response_produces_no_answer(synthesizer, litellm_call):
    litellm_call.return_value = model_reply(raw="Sure! Here's my answer: 42.")

    with pytest.raises(SynthesisResponseError):
        await synthesizer.synthesize(query="Anything")


@pytest.mark.asyncio
async def test_empty_model_answer_is_rejected(synthesizer, litellm_call):
    litellm_call.return_value = model_reply(answer="")

    with pytest.raises(SynthesisResponseError):
        await synthesizer.synthesize(query="Anything")


@pytest.mark.asyncio
async def test_brief_mode_asks_for_a_short_answer_and_returns_it_bare(synthesizer, litellm_call):
    """The graph's automatic path: 2-4 sentences, no "To make this citable"
    section -- the synthesis node writes that answer's disclosure line."""
    litellm_call.return_value = model_reply(answer="Deep learning is a subset of ML.")

    result = await synthesizer.synthesize(query="How do ML and DL differ?", brief=True)

    assert "2-4" in sent_messages(litellm_call)[1]["content"]
    assert result.answer == "Deep learning is a subset of ML."
    assert result.citations == []
    assert result.grounding_status is ResearchGroundingStatus.UNSOURCED


# ---------------------------------------------------------------------------
# Never a grounded synthesis strategy
# ---------------------------------------------------------------------------


def test_get_synthesizer_never_returns_the_unsourced_path(monkeypatch):
    """`UnsourcedSynthesizer` must never be the graph's synthesis strategy
    -- it is reached only as the separately injected general-knowledge
    path (`GraphDependencies.unsourced_synthesizer`) and the dedicated
    endpoint."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "synthesis_grounded", True)
    assert not isinstance(get_synthesizer(), UnsourcedSynthesizer)

    monkeypatch.setattr(settings, "synthesis_grounded", False)
    assert not isinstance(get_synthesizer(), UnsourcedSynthesizer)


# ---------------------------------------------------------------------------
# Service integration -- a real linked run row, against the real database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_unsourced_run_persists_a_separate_linked_row(
    session, project, litellm_call
):
    """The original run must be untouched; a new, separately auditable
    row records the deliberate general-knowledge answer."""
    from app.modules.research.enums import ResearchRunStatus
    from app.modules.research.models import ResearchRun
    from app.modules.research.service import ResearchService

    litellm_call.return_value = model_reply(
        answer="A general-knowledge answer.", would_need="a direct measurement."
    )

    original = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query="What is the exact figure this project's papers don't cover?",
        status=ResearchRunStatus.COMPLETED,
        include_assets=True,
        include_web=False,
        max_results=5,
        grounding_status=ResearchGroundingStatus.INSUFFICIENT_EVIDENCE,
        citations=[],
    )
    session.add(original)
    await session.flush()
    await session.commit()

    service = ResearchService(session)
    created = await service.create_unsourced_run(original)

    assert created.id != original.id
    assert created.query == original.query
    assert created.project_id == original.project_id
    assert created.owner_id == original.owner_id
    assert created.status is ResearchRunStatus.COMPLETED
    assert created.grounding_status is ResearchGroundingStatus.UNSOURCED
    assert created.citations == []
    assert "Not in your uploaded papers" in created.final_answer

    # The original is untouched -- it stays the honest record of the
    # platform's own decline to answer.
    await session.refresh(original)
    assert original.grounding_status is ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    assert original.id != created.id


@pytest.mark.asyncio
async def test_create_unsourced_run_persists_failure_when_the_model_call_fails(
    session, project, litellm_call
):
    """A failed model call must still leave an auditable `FAILED` row."""
    from app.modules.research.enums import ResearchRunStatus
    from app.modules.research.models import ResearchRun
    from app.modules.research.service import ResearchService, UnsourcedSynthesisFailedError

    litellm_call.side_effect = RuntimeError("ollama exploded")

    original = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query="Anything",
        status=ResearchRunStatus.COMPLETED,
        include_assets=True,
        include_web=False,
        max_results=5,
        grounding_status=ResearchGroundingStatus.INSUFFICIENT_EVIDENCE,
        citations=[],
    )
    session.add(original)
    await session.flush()
    await session.commit()

    service = ResearchService(session)
    with pytest.raises(UnsourcedSynthesisFailedError):
        await service.create_unsourced_run(original)
