# Paper Suggestions (OpenAlex) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a research run's final synthesis pass, detect evidence gaps in the answer and — when a gap exists and OpenAlex is configured — search OpenAlex for papers that could fill it, persist them on the run, and show them in a panel next to `EvidenceGapPanel`. Papers are never fed into synthesis.

**Architecture:** A new non-critical agent `paper_suggestion`, registered in `AGENT_REGISTRY` and wired into the graph after the bounded web-fallback loop settles. It runs a small, dedicated LLM call (`GapDetector`, JSON-schema-enforced, reusing the existing `ResearchGap` shape) over the *already-finished* `final_answer` — deliberately **not** an extension of `GroundedSynthesisResponse`/`GroundedSynthesizer`, so the heavily-tested grounded-synthesis engine (`synthesis.py`, `test_grounded_synthesis.py`) is untouched. When gaps are found, `OpenAlexProvider` (direct `httpx`, no SDK, same shape as `TavilyWebResearchProvider`) searches OpenAlex and the results are stored on `research_runs.suggested_papers` (new JSONB column). Whether the node is even entered is a pure function of state (`paper_suggestion_eligible`, set once in `synthesis_node` from whether an OpenAlex key is configured) — so "no key" is a true graph-level skip (`ResearchStepStatus.SKIPPED`, exactly like the existing web-research skip), while "no gaps" is a completed step that simply suggests nothing.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, LangGraph, httpx, pytest + httpx `ASGITransport`/`MockTransport`; React, TypeScript, TanStack Query, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-13-persona-features-design.md` (section 2 "Paper suggestions", section 7 error-handling rows for it, section 8 testing rows for it, build-order step 2).

## Documented deviation from the spec text

The spec says paper suggestions trigger "when synthesis reports `missing_information`". In the actual codebase, the research-run synthesis path (`GroundedSynthesisResponse` / `SynthesisResult` in `backend/app/agents/planner/synthesis.py`) has no such field — `missing_information: list[ResearchGap]` exists only on a separate, per-document feature (`AnalyzeDocumentResponse`/`experiment_service.analyze_document`, used by `EvidenceGapPanel.tsx`'s own `/documents/{asset_id}/analyze` call). Extending the grounded-synthesis engine to add gap reporting was evaluated and rejected (per user decision on 2026-09-14) because it would touch the critical, exhaustively-tested grounded-synthesis prompt/parsing/schema for a "step 2 only" change. Instead, `paper_suggestion_node` runs its **own** small, separate, non-critical LLM call (`GapDetector`) over the finished answer, reusing the existing `ResearchGap` Pydantic model so there is still exactly one gap shape in the codebase. This means:

- Gap detection happens once per run, after every final synthesis pass where OpenAlex is configured — not only when `grounding_status` is `insufficient_evidence`.
- "No OpenAlex key" is recorded as a true `SKIPPED` step (the graph never enters the node).
- "No gaps found" is recorded as a `COMPLETED` step with an empty `suggested_papers` list, not a `SKIPPED` step — the node did run, it simply had nothing to suggest. The frontend panel doesn't care either way: it renders only when `suggested_papers` is non-empty.

## Global Constraints

- Papers are **never** passed into synthesis. `paper_suggestion_node` runs strictly after `synthesis_node` in the graph and never touches `final_answer`, `citations`, `visualization`, or `equations`.
- `OpenAlexProvider` follows the `TavilyWebResearchProvider` pattern exactly: direct `httpx` calls, no SDK.
- Settings: `openalex_api_key: SecretStr | None = None`, `openalex_timeout: float = Field(default=20.0, gt=0)`, read from `.env` as `OPENALEX_API_KEY` / `OPENALEX_TIMEOUT`. A blank key is treated as unset, via a `field_validator` identical in shape to `blank_tavily_key_is_unset`.
- `suggested_papers` entry shape, exactly: `openalex_id`, `title`, `authors` (list[str]), `year` (int | None), `cited_by_count` (int), `landing_url` (str), `oa_pdf_url` (str | None), `relevance_note` (str).
- The search query is the user's question plus every gap's `description`, space-joined (spec section 2).
- OpenAlex down/timeout: `paper_suggestion` fails non-critically (`critical=False` in `AGENT_REGISTRY`); the run and its answer are unaffected.
- No OpenAlex key: the step is skipped with an explicit reason; never simulated results.
- Never commit the real OpenAlex key. `.env`/`.env.example` get a blank `OPENALEX_API_KEY=` line only.
- Alembic only, no manual schema changes. Current head is `a41c7d2e9f05`; the new migration's `down_revision` is `a41c7d2e9f05`.
- Scope is build-order step 2 only: no "Add & re-run" (no import endpoint, no Celery chain, no `parent_run_id`), no synopsis, no build plan.
- Backend commands run as `docker exec aikdap_backend <cmd>` from `/app` (bind-mounted `backend/`, auto-reload). Frontend commands run from `frontend/` on the host. Backend runs on `:8001`.
- Known environmental test failures to ignore: `test_execution_*` (no Docker in the container), `test_cross_paper`, `test_fallback_grounding` (live LLM providers).
- After backend code changes, run `python -m graphify update .` from the repo root.
- Commits: one feature per commit, message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: OpenAlex settings

**Files:**
- Modify: `backend/app/core/config/settings.py`
- Modify: `.env.example`
- Modify: `.env`
- Test: `backend/tests/test_paper_suggestion.py` (created here, extended in Task 2)

**Interfaces:**
- Produces: `Settings.openalex_api_key: SecretStr | None`, `Settings.openalex_timeout: float`, `Settings.blank_openalex_key_is_unset`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_paper_suggestion.py`:

```python
"""OpenAlex settings, provider, abstract reconstruction, and gap detection
(Milestone 10 step 2 -- spec section 2)."""

from pydantic import SecretStr

from app.core.config.settings import Settings


def test_openalex_settings_default_to_unconfigured():
    settings = Settings(_env_file=None)
    assert settings.openalex_api_key is None
    assert settings.openalex_timeout == 20.0


def test_blank_openalex_key_is_treated_as_unset():
    assert Settings.blank_openalex_key_is_unset("") is None
    assert Settings.blank_openalex_key_is_unset("   ") is None
    assert Settings.blank_openalex_key_is_unset("real-key") == "real-key"
    assert Settings.blank_openalex_key_is_unset(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec aikdap_backend python -m pytest tests/test_paper_suggestion.py -v`
Expected: FAIL — `Settings` has no attribute `openalex_api_key` / `blank_openalex_key_is_unset`.

- [ ] **Step 3: Add the settings**

In `backend/app/core/config/settings.py`, immediately after the existing `blank_tavily_key_is_unset` validator (the block ending at `tavily_timeout`/`blank_tavily_key_is_unset`), add:

```python
    #: OpenAlex Works API. Unconfigured means paper suggestions are
    #: skipped entirely (see `agents.planner.graph`'s `paper_suggestion`
    #: routing) rather than answered with simulated results.
    openalex_api_key: SecretStr | None = None
    openalex_timeout: float = Field(default=20.0, gt=0)

    @field_validator("openalex_api_key", mode="before")
    @classmethod
    def blank_openalex_key_is_unset(cls, value: object) -> object:
        """A bare `OPENALEX_API_KEY=` in .env means "not configured".

        Same rationale as `blank_tavily_key_is_unset`: without this it
        parses as `SecretStr("")` rather than `None`, and an empty key
        would switch on a "configured" provider that can only ever fail.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value
```

- [ ] **Step 4: Add blank entries to the env files**

In `.env.example`, immediately after the `TAVILY_API_KEY=` line, add:

```
OPENALEX_API_KEY=
OPENALEX_TIMEOUT=20.0
```

In `.env` (the root file the `backend`/`worker` containers load via `env_file: .env` in `docker-compose.yml` — gitignored and not tracked by git, confirmed via `git ls-files .env` returning nothing), immediately after the `TAVILY_API_KEY=...` line, add:

```
OPENALEX_API_KEY=
```

Leave it blank — never put a real key in this file as part of this change. This edit is local only and is never committed (see Step 6). `OPENALEX_TIMEOUT` needs no entry here; the `20.0` default is enough for now.

- [ ] **Step 5: Run test to verify it passes**

Run: `docker exec aikdap_backend python -m pytest tests/test_paper_suggestion.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/config/settings.py backend/tests/test_paper_suggestion.py .env.example
git commit -m "feat(paper-suggestions): add OpenAlex settings

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Note: `.env` is gitignored and not tracked (confirmed above) — its edit in Step 4 is local-only and intentionally excluded from this commit.

---

### Task 2: `GapDetectionResponse` schema, gap-detection prompt, and `paper_suggestion.py`

**Files:**
- Modify: `backend/app/modules/research/schemas.py`
- Modify: `backend/app/agents/planner/prompts.py`
- Create: `backend/app/agents/planner/paper_suggestion.py`
- Test: `backend/tests/test_paper_suggestion.py` (extended)

**Interfaces:**
- Consumes: `Settings.openalex_api_key`, `Settings.openalex_timeout` (Task 1); `ResearchGap`, `GapClassification` (already exist in `research/schemas.py`); `LLMGateway`, `get_llm_gateway`, `LLMResponse` (`app.core.llm`); `settings.synthesis_model`.
- Produces: `app.modules.research.schemas.GapDetectionResponse`; `app.agents.planner.prompts.GAP_DETECTION_SYSTEM_PROMPT`, `render_gap_detection_prompt(*, query, answer, grounding_status) -> str`; `app.agents.planner.paper_suggestion.SuggestedPaper` (TypedDict), `OpenAlexProvider(api_key: SecretStr, timeout: float, transport=None)` with `async def search(self, *, query: str, limit: int) -> list[SuggestedPaper]`, `GapDetector(gateway=None, model=None)` with `async def detect(self, *, query: str, answer: str, grounding_status: str) -> list[ResearchGap]`, `build_search_query(query: str, gaps: list[ResearchGap]) -> str`, `build_relevance_note(gaps: list[ResearchGap]) -> str`, `_reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str`, `_parse_gaps(content: str) -> list[ResearchGap]`.

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/test_paper_suggestion.py` with (keeping the Task 1 tests and adding the rest):

```python
"""OpenAlex settings, provider, abstract reconstruction, and gap detection
(Milestone 10 step 2 -- spec section 2)."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config.settings import Settings


def test_openalex_settings_default_to_unconfigured():
    settings = Settings(_env_file=None)
    assert settings.openalex_api_key is None
    assert settings.openalex_timeout == 20.0


def test_blank_openalex_key_is_treated_as_unset():
    assert Settings.blank_openalex_key_is_unset("") is None
    assert Settings.blank_openalex_key_is_unset("   ") is None
    assert Settings.blank_openalex_key_is_unset("real-key") == "real-key"
    assert Settings.blank_openalex_key_is_unset(None) is None


# ---------------------------------------------------------------------------
# OpenAlexProvider
# ---------------------------------------------------------------------------

from app.agents.planner.paper_suggestion import (  # noqa: E402
    GapDetector,
    OpenAlexProvider,
    _parse_gaps,
    _reconstruct_abstract,
    build_relevance_note,
    build_search_query,
)
from app.modules.research.schemas import GapClassification, GapDetectionResponse, ResearchGap  # noqa: E402


def _work(**overrides):
    base = {
        "id": "https://openalex.org/W123",
        "display_name": "Deep Learning for Poultry Disease Detection",
        "publication_year": 2023,
        "cited_by_count": 42,
        "authorships": [
            {"author": {"display_name": "A. Researcher"}},
            {"author": {"display_name": "B. Scientist"}},
        ],
        "primary_location": {"landing_page_url": "https://example.org/w123"},
        "best_oa_location": {"pdf_url": "https://example.org/w123.pdf"},
        "abstract_inverted_index": {"Cross-encoders": [0], "score": [1], "pairs.": [2]},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_openalex_results_are_parsed_into_suggested_papers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search"] == "poultry disease detection"
        assert request.url.params["per_page"] == "5"
        return httpx.Response(200, json={"results": [_work()]})

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    papers = await provider.search(query="poultry disease detection", limit=5)

    assert len(papers) == 1
    paper = papers[0]
    assert paper["openalex_id"] == "https://openalex.org/W123"
    assert paper["title"] == "Deep Learning for Poultry Disease Detection"
    assert paper["authors"] == ["A. Researcher", "B. Scientist"]
    assert paper["year"] == 2023
    assert paper["cited_by_count"] == 42
    assert paper["landing_url"] == "https://example.org/w123"
    assert paper["oa_pdf_url"] == "https://example.org/w123.pdf"


@pytest.mark.asyncio
async def test_result_with_no_id_or_title_is_dropped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_work(id=None), _work(display_name=None)]})

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    papers = await provider.search(query="q", limit=5)
    assert papers == []


@pytest.mark.asyncio
async def test_abstract_is_rebuilt_from_the_inverted_index_into_the_relevance_note():
    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]})),
    )
    papers = await provider.search(query="q", limit=5)

    assert "Cross-encoders score pairs." in papers[0]["relevance_note"]


@pytest.mark.asyncio
async def test_paper_with_no_abstract_gets_a_plain_relevance_note():
    work = _work(abstract_inverted_index=None)
    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [work]})),
    )
    papers = await provider.search(query="q", limit=5)

    assert papers[0]["relevance_note"] == "No abstract available from OpenAlex."


@pytest.mark.asyncio
async def test_openalex_timeout_raises_for_the_non_critical_node_to_record():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(httpx.TimeoutException):
        await provider.search(query="q", limit=5)


@pytest.mark.asyncio
async def test_openalex_http_error_raises_for_the_non_critical_node_to_record():
    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "down"})),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search(query="q", limit=5)


def test_reconstruct_abstract_orders_words_by_position():
    inverted_index = {"score.": [2], "Cross-encoders": [0], "pairs": [1]}
    assert _reconstruct_abstract(inverted_index) == "Cross-encoders pairs score."


def test_reconstruct_abstract_handles_missing_index():
    assert _reconstruct_abstract(None) == ""
    assert _reconstruct_abstract({}) == ""


def test_get_paper_provider_returns_none_without_a_key(monkeypatch):
    from app.agents.planner.nodes import get_paper_provider
    from app.core.config.settings import settings

    monkeypatch.setattr(settings, "openalex_api_key", None)
    assert get_paper_provider() is None


def test_get_paper_provider_returns_a_configured_provider_with_a_key(monkeypatch):
    from app.agents.planner.nodes import get_paper_provider
    from app.core.config.settings import settings

    monkeypatch.setattr(settings, "openalex_api_key", SecretStr("test-key"))
    provider = get_paper_provider()
    assert isinstance(provider, OpenAlexProvider)


# ---------------------------------------------------------------------------
# Search query and relevance note
# ---------------------------------------------------------------------------


def _gap(**overrides) -> ResearchGap:
    base = dict(
        gap_type="baseline comparison",
        classification=GapClassification.REQUIRED,
        description="a comparison against a non-deep-learning baseline",
        why_needed="to judge whether the reported gain is real",
    )
    base.update(overrides)
    return ResearchGap(**base)


def test_build_search_query_combines_question_and_gap_descriptions():
    gaps = [
        _gap(description="a comparison against a non-deep-learning baseline"),
        _gap(gap_type="dataset size", description="the training set's total sample count"),
    ]
    query = build_search_query("What improves poultry disease detection accuracy?", gaps)
    assert query == (
        "What improves poultry disease detection accuracy? "
        "a comparison against a non-deep-learning baseline "
        "the training set's total sample count"
    )


def test_build_relevance_note_summarizes_the_gaps():
    note = build_relevance_note([_gap()])
    assert note == "Suggested to help address: a comparison against a non-deep-learning baseline"


# ---------------------------------------------------------------------------
# GapDetector
# ---------------------------------------------------------------------------


class FakeGateway:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        return LLMResponse(content=self._content, model="fake-model", provider="fake", latency_ms=5)


@pytest.mark.asyncio
async def test_gap_detector_parses_the_model_json_schema_response():
    content = json.dumps(
        {
            "gaps": [
                {
                    "gap_type": "baseline comparison",
                    "classification": "required",
                    "description": "no comparison to a simple baseline",
                    "why_needed": "to know if the improvement is meaningful",
                    "search_intent": "poultry disease detection baseline comparison",
                }
            ]
        }
    )
    detector = GapDetector(gateway=FakeGateway(content), model="fake-model")
    gaps = await detector.detect(query="q", answer="an answer [c1].", grounding_status="grounded")

    assert len(gaps) == 1
    assert gaps[0].gap_type == "baseline comparison"
    assert gaps[0].classification is GapClassification.REQUIRED
    assert gaps[0].search_intent == "poultry disease detection baseline comparison"


@pytest.mark.asyncio
async def test_gap_detector_returns_no_gaps_when_the_model_reports_none():
    detector = GapDetector(gateway=FakeGateway(json.dumps({"gaps": []})), model="fake-model")
    assert await detector.detect(query="q", answer="a complete answer.", grounding_status="grounded") == []


@pytest.mark.asyncio
async def test_gap_detector_returns_no_gaps_for_an_empty_answer_without_calling_the_model():
    gateway = FakeGateway(json.dumps({"gaps": []}))
    detector = GapDetector(gateway=gateway, model="fake-model")

    assert await detector.detect(query="q", answer="", grounding_status="insufficient_evidence") == []
    assert gateway.calls == []


def test_parse_gaps_degrades_to_empty_list_on_unparseable_content():
    assert _parse_gaps("not json") == []


def test_parse_gaps_degrades_to_empty_list_on_schema_mismatch():
    assert _parse_gaps(json.dumps({"gaps": [{"gap_type": "x"}]})) == []


def test_parse_gaps_strips_a_json_fence():
    content = "```json\n" + json.dumps({"gaps": []}) + "\n```"
    assert _parse_gaps(content) == []


def test_gap_detection_response_reuses_the_existing_research_gap_shape():
    response = GapDetectionResponse.model_validate({"gaps": [_gap().model_dump()]})
    assert response.gaps[0].gap_type == "baseline comparison"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend python -m pytest tests/test_paper_suggestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.planner.paper_suggestion'` and `ImportError: cannot import name 'GapDetectionResponse'`.

- [ ] **Step 3: Add `GapDetectionResponse` to the research schemas**

In `backend/app/modules/research/schemas.py`, immediately after the `ResearchGap` class (right before `class ResearchConflict(BaseModel):`), add:

```python
class GapDetectionResponse(BaseModel):
    """The JSON-schema-enforced envelope `GapDetector` requests.

    Reuses `ResearchGap` rather than defining a second gap shape, so a
    "missing information" item means the same thing everywhere in the
    codebase, whether it came from per-document analysis
    (`analyze_document`) or from `paper_suggestion_node`'s own
    post-synthesis check.
    """

    gaps: list[ResearchGap] = Field(default_factory=list)

```

- [ ] **Step 4: Add the gap-detection prompt**

In `backend/app/agents/planner/prompts.py`, at the end of the file, add:

```python


# ---------------------------------------------------------------------------
# Evidence-gap detection for paper suggestions (Milestone 10 step 2)
# ---------------------------------------------------------------------------
#
# Reached only after synthesis has already produced `final_answer` --
# this never influences that answer, it only looks at it afterward to
# decide whether OpenAlex is worth searching. Kept entirely separate
# from `GROUNDED_SYNTHESIS_SYSTEM_PROMPT` so extending or tuning this
# can never perturb the grounded-synthesis contract.

GAP_DETECTION_SYSTEM_PROMPT = """You are the AIKDAP evidence-gap detector.

You are given a question and the answer AIKDAP already produced for it.
Your only job is to identify what additional evidence -- if any -- would
meaningfully strengthen that answer. You do not answer the question
yourself and you do not critique its wording.

Respond with a single JSON object and nothing else:

{
  "gaps": [
    {
      "gap_type": "a short label, e.g. 'missing baseline comparison'",
      "classification": "required" | "helpful" | "optional" | "ambiguous",
      "description": "what is missing, in plain language",
      "why_needed": "why this matters for answering the question well",
      "search_intent": "a short phrase suitable for a literature search, or null"
    }
  ]
}

Rules:
- If the answer is already well supported and nothing more would meaningfully help, return {"gaps": []}. Do not invent a gap to have something to report.
- "required" means the answer is not trustworthy without it; "helpful" means it would add confidence or depth; "optional" means it is nice-to-have; "ambiguous" means you cannot tell.
- Never invent facts about what a missing source would say -- describe only what is absent."""

GAP_DETECTION_USER_TEMPLATE = """Question:
{query}

Answer given:
{answer}

Grounding status: {grounding_status}

Identify any evidence gaps a reader would still need filled, if any."""


def render_gap_detection_prompt(*, query: str, answer: str, grounding_status: str) -> str:
    """Render the full gap-detection prompt exactly as it would be sent."""
    return "\n\n".join(
        (
            GAP_DETECTION_SYSTEM_PROMPT,
            GAP_DETECTION_USER_TEMPLATE.format(
                query=query,
                answer=answer or "(no answer produced)",
                grounding_status=grounding_status or "unknown",
            ),
        )
    )
```

- [ ] **Step 5: Create `paper_suggestion.py`**

Create `backend/app/agents/planner/paper_suggestion.py`:

```python
"""OpenAlex paper suggestions and the evidence-gap detector that drives them.

`paper_suggestion_node` (`nodes.py`) runs strictly after the research
answer is final -- reached only once `graph.py`'s bounded web-fallback
loop has settled. Nothing in this module is ever read by
`synthesis_node`, and no paper found here can become evidence for
`final_answer`: that is what makes "papers are never fed into
synthesis" (spec section 2) true by construction, not by convention.

Kept in its own module rather than folded into `synthesis.py` or
`nodes.py`, so extending paper suggestions later never touches the
grounded-synthesis engine `test_grounded_synthesis.py` already
exercises exhaustively.
"""

import re
from typing import TypedDict

import httpx
from pydantic import SecretStr, ValidationError

from app.agents.planner.prompts import GAP_DETECTION_SYSTEM_PROMPT, render_gap_detection_prompt
from app.core.config.settings import settings
from app.core.llm import LLMGateway, get_llm_gateway
from app.core.logging.logger import get_logger
from app.modules.research.schemas import GapDetectionResponse, ResearchGap

logger = get_logger(__name__)

#: Strips a ```json ... ``` fence, the same tolerance `synthesis._parse_response` applies.
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

#: Longest abstract excerpt folded into a paper's `relevance_note`.
_ABSTRACT_NOTE_CHARACTERS = 280


class SuggestedPaper(TypedDict):
    """One OpenAlex work suggested to help close a gap in an answer.

    Persisted verbatim as one entry of `research_runs.suggested_papers`
    (JSONB) -- see `models.ResearchRun.suggested_papers` and
    `schemas.ResearchRunRead.suggested_papers`.
    """

    openalex_id: str
    title: str
    authors: list[str]
    year: int | None
    cited_by_count: int
    landing_url: str
    oa_pdf_url: str | None
    relevance_note: str


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Rebuild an abstract from OpenAlex's `abstract_inverted_index`.

    OpenAlex never returns abstracts as plain text (licensing) -- only
    this word -> position mapping, one entry per distinct word with
    every position it occurs at. Absent (`""`) for a paper with none.
    """
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, indices in inverted_index.items():
        for index in indices:
            positions[index] = word
    return " ".join(positions[index] for index in sorted(positions))


def build_search_query(query: str, gaps: list[ResearchGap]) -> str:
    """The user's question plus every gap's description (spec section 2)."""
    return " ".join([query, *(gap.description for gap in gaps)])


def build_relevance_note(gaps: list[ResearchGap]) -> str:
    """A single honest note attached to every suggestion from one search.

    Not per-paper: the search is one combined query over every gap
    (`build_search_query`), and OpenAlex gives no "why this paper"
    signal -- so the note states what prompted the search rather than
    inventing a paper-specific judgement no data supports. Prefixed
    (not replaced) onto each paper's own abstract-derived note by
    `paper_suggestion_node`.
    """
    return "Suggested to help address: " + "; ".join(gap.description for gap in gaps)


class GapDetector:
    """Identifies evidence gaps in a completed answer via one small,
    dedicated LLM call.

    Deliberately separate from `GroundedSynthesizer`
    (`synthesis.GroundedSynthesizer`): gaps are detected *after* the
    answer is already final, over the answer text itself, so extending
    or tuning this can never perturb the grounded-synthesis prompt,
    schema, or parsing.
    """

    name = "gap_detector_llm_v1"

    def __init__(self, *, gateway: LLMGateway | None = None, model: str | None = None) -> None:
        self._gateway = gateway or get_llm_gateway()
        self._model = model or settings.synthesis_model

    async def detect(self, *, query: str, answer: str, grounding_status: str) -> list[ResearchGap]:
        """Return the gaps a reader would still need filled, if any.

        No model call for an empty answer: there is nothing to check
        gaps against, and asking would only invite an invented one.
        """
        if not answer.strip():
            return []

        prompt = render_gap_detection_prompt(
            query=query, answer=answer, grounding_status=grounding_status
        )
        response = await self._gateway.generate(
            prompt=prompt,
            system_prompt=GAP_DETECTION_SYSTEM_PROMPT,
            model=self._model,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "GapDetectionResponse",
                    "schema": GapDetectionResponse.model_json_schema(),
                },
            },
        )
        gaps = _parse_gaps(response.content)
        logger.info("gap_detection_completed", model=self._model, gap_count=len(gaps))
        return gaps


def _parse_gaps(content: str) -> list[ResearchGap]:
    """Parse the model's gap list, degrading to no gaps on anything unusable.

    Never raises: an unparseable gap-detection response should not fail
    the run (this step is non-critical) or hide a perfectly good answer
    behind an error -- it should simply mean no papers are suggested
    this time.
    """
    match = _JSON_FENCE.match(content)
    text = match.group(1) if match else content
    try:
        return GapDetectionResponse.model_validate_json(text).gaps
    except (ValidationError, ValueError) as exc:
        logger.warning("gap_detection_response_unparseable", error=str(exc))
        return []


class OpenAlexProvider:
    """Searches OpenAlex's Works API for papers that could fill an evidence gap.

    Direct `httpx` calls, no SDK -- the same choice
    `nodes.TavilyWebResearchProvider` makes and for the same reason: one
    GET request does not justify a dependency. Raises on any failure;
    `paper_suggestion` is non-critical in the registry, so a down or
    slow OpenAlex fails only this step and the run's answer is
    unaffected.
    """

    name = "openalex_v1"
    endpoint = "https://api.openalex.org/works"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        # Injected only by tests, so request building and parsing run
        # against a `MockTransport` rather than being stubbed out.
        self._transport = transport

    async def search(self, *, query: str, limit: int) -> list[SuggestedPaper]:
        """Return up to `limit` papers OpenAlex judges relevant to `query`."""
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                self.endpoint,
                # OpenAlex's premium tier authenticates via a query
                # parameter, not a header -- unlike Tavily. Still only
                # ever read from `settings`, never hardcoded.
                params={
                    "search": query,
                    "per_page": limit,
                    "api_key": self._api_key.get_secret_value(),
                },
            )
        response.raise_for_status()

        papers: list[SuggestedPaper] = []
        for item in response.json().get("results") or []:
            openalex_id, title = item.get("id"), item.get("display_name") or item.get("title")
            # A result with no id or no title is nothing a card can
            # show or a citation-like link can point at.
            if not openalex_id or not title:
                continue

            authors = [
                authorship["author"]["display_name"]
                for authorship in item.get("authorships") or []
                if (authorship.get("author") or {}).get("display_name")
            ]
            location = item.get("primary_location") or {}
            best_oa = item.get("best_oa_location") or {}
            abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))
            note = (
                f"Abstract: {abstract[:_ABSTRACT_NOTE_CHARACTERS]}"
                f"{'...' if len(abstract) > _ABSTRACT_NOTE_CHARACTERS else ''}"
                if abstract
                else "No abstract available from OpenAlex."
            )

            papers.append(
                SuggestedPaper(
                    openalex_id=openalex_id,
                    title=title,
                    authors=authors,
                    year=item.get("publication_year"),
                    cited_by_count=int(item.get("cited_by_count") or 0),
                    landing_url=location.get("landing_page_url") or openalex_id,
                    oa_pdf_url=best_oa.get("pdf_url"),
                    relevance_note=note,
                )
            )
        return papers[:limit]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_paper_suggestion.py -v`
Expected: all passed (~25 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/modules/research/schemas.py backend/app/agents/planner/prompts.py backend/app/agents/planner/paper_suggestion.py backend/tests/test_paper_suggestion.py
git commit -m "feat(paper-suggestions): add OpenAlexProvider and the evidence-gap detector

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Graph wiring — `paper_suggestion` node, routing, and registry

**Files:**
- Modify: `backend/app/agents/planner/state.py`
- Modify: `backend/app/agents/planner/nodes.py`
- Modify: `backend/app/agents/planner/registry.py`
- Modify: `backend/app/agents/planner/graph.py`
- Modify: `backend/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `OpenAlexProvider`, `GapDetector`, `SuggestedPaper`, `build_search_query`, `build_relevance_note` (Task 2); `settings.openalex_api_key`, `settings.openalex_timeout` (Task 1).
- Produces: `ResearchNode.PAPER_SUGGESTION`; `ResearchState.suggested_papers`, `ResearchState.paper_suggestion_eligible`; `GraphDependencies.paper_provider`, `GraphDependencies.gap_detector`; `get_paper_provider() -> OpenAlexProvider | None`; `paper_suggestion_node`; `AGENT_REGISTRY["paper_suggestion"]`; the compiled graph's `synthesis -> paper_suggestion -> END` edge.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_orchestrator.py`, make these changes:

1. Add `"paper_suggestion"` to `EXPECTED_AGENTS` (around line 63):

```python
EXPECTED_AGENTS = {
    "planner",
    "router",
    "web_research",
    "asset_retrieval",
    "context_builder",
    "synthesis",
    "paper_suggestion",
}
```

2. Add `from app.modules.research.schemas import ResearchGap` to the imports near the top (after the existing `app.modules.research.enums` import).

3. Add two fakes after `BrokenPlanner` (around line 117):

```python
class FakeGapDetector:
    """Returns a fixed gap list without calling a model."""

    name = "fake_gap_detector"

    def __init__(self, gaps: list[ResearchGap]) -> None:
        self._gaps = gaps

    async def detect(self, *, query, answer, grounding_status):
        return self._gaps


class FakePaperProvider:
    """Returns a fixed paper list and records every call it received."""

    name = "fake_paper_provider"

    def __init__(self, papers: list[dict] | None = None, *, fail: bool = False) -> None:
        self._papers = papers or []
        self._fail = fail
        self.calls: list[dict] = []

    async def search(self, *, query, limit):
        self.calls.append({"query": query, "limit": limit})
        if self._fail:
            raise RuntimeError("openalex unavailable")
        return self._papers
```

4. In `test_successful_run_persists_answer_citations_and_steps` (around line 712-750), the exact step order and unpacking need one more trailing skipped step (`paper_suggestion`, since default `make_dependencies()` sets no `paper_provider`). Replace the block from `assert [s.node_name for s in steps]` through the end of the test with:

```python
    assert [s.node_name for s in steps] == [
        "planner",
        "router",
        "asset_retrieval",
        "context_builder",
        "synthesis",
        "web_research",
        "paper_suggestion",
    ]
    *executed, web, papers = steps
    for step in executed:
        assert step.status is ResearchStepStatus.COMPLETED
        assert step.duration_ms is not None
        assert step.started_at and step.completed_at
        assert step.title and step.summary
    assert web.status is ResearchStepStatus.SKIPPED
    assert web.summary.startswith("Not needed")
    assert papers.status is ResearchStepStatus.SKIPPED
    assert papers.summary == "No OpenAlex API key is configured (set OPENALEX_API_KEY)."

    # No registered node silently disappears from the trace.
    assert {s.node_name for s in steps} == EXPECTED_AGENTS
```

5. Add four new tests at the end of the file (before the final blank line), reusing `_run_to_completion`, `make_dependencies`, `make_config`, `initial_state`, `RecordingTracker`:

```python
# ---------------------------------------------------------------------------
# TEST 10, 11, 12, 13 — paper_suggestion: gaps drive it, failure is non-critical
# ---------------------------------------------------------------------------


def _gap(description: str = "no comparison against a simple baseline") -> ResearchGap:
    return ResearchGap(
        gap_type="baseline comparison",
        classification="required",
        description=description,
        why_needed="to judge whether the reported gain is real",
    )


@pytest.mark.asyncio
async def test_paper_suggestion_runs_and_persists_papers_when_gaps_are_found():
    """TEST 10 -- gaps found + a configured provider -> papers persisted."""
    tracker = RecordingTracker()
    paper_provider = FakePaperProvider(
        papers=[
            {
                "openalex_id": "https://openalex.org/W1",
                "title": "A relevant paper",
                "authors": ["A. Author"],
                "year": 2022,
                "cited_by_count": 10,
                "landing_url": "https://example.org/w1",
                "oa_pdf_url": None,
                "relevance_note": "Abstract: ...",
            }
        ]
    )
    dependencies = make_dependencies(
        gap_detector=FakeGapDetector([_gap()]), paper_provider=paper_provider
    )
    graph = build_research_graph().compile()

    final_state = await graph.ainvoke(initial_state(), config=make_config(dependencies, tracker))

    assert final_state["suggested_papers"][0]["openalex_id"] == "https://openalex.org/W1"
    assert "Suggested to help address" in final_state["suggested_papers"][0]["relevance_note"]
    assert paper_provider.calls[0]["query"].startswith(QUERY)
    assert "no comparison against a simple baseline" in paper_provider.calls[0]["query"]
    assert ("paper_suggestion", "RuntimeError", False) not in tracker.failed
    assert "paper_suggestion" in [node for node, _ in tracker.succeeded]


@pytest.mark.asyncio
async def test_paper_suggestion_skips_openalex_search_when_no_gaps_are_found():
    """TEST 11 -- no gaps -> the node completes without calling OpenAlex."""
    paper_provider = FakePaperProvider(papers=[{"openalex_id": "should-not-be-used"}])
    dependencies = make_dependencies(gap_detector=FakeGapDetector([]), paper_provider=paper_provider)
    graph = build_research_graph().compile()

    final_state = await graph.ainvoke(initial_state(), config=make_config(dependencies))

    assert not final_state.get("suggested_papers")
    assert paper_provider.calls == []


@pytest.mark.asyncio
async def test_paper_suggestion_node_never_runs_without_an_openalex_key():
    """TEST 12 -- unconfigured (default deps) -> the node is never entered."""
    tracker = RecordingTracker()
    graph = build_research_graph().compile()

    await graph.ainvoke(initial_state(), config=make_config(make_dependencies(), tracker))

    assert "paper_suggestion" not in tracker.started


@pytest.mark.asyncio
async def test_paper_suggestion_failure_is_non_critical():
    """TEST 13 -- OpenAlex down/timing out fails only this step."""
    tracker = RecordingTracker()
    dependencies = make_dependencies(
        gap_detector=FakeGapDetector([_gap()]), paper_provider=FakePaperProvider(fail=True)
    )
    graph = build_research_graph().compile()

    final_state = await graph.ainvoke(initial_state(), config=make_config(dependencies, tracker))

    assert final_state["final_answer"], "the answer must be unaffected by a paper-suggestion failure"
    assert not final_state.get("suggested_papers")
    assert ("paper_suggestion", "RuntimeError", False) in tracker.failed


@pytest.mark.asyncio
async def test_paper_suggestion_skip_reason_via_the_execution_service(session, project):
    """TEST 14 -- end to end through the real database: skip reason persisted."""
    run = await _run_to_completion(session, project, dependencies=make_dependencies())

    steps = {s.node_name: s for s in await ResearchStepRepository(session).list_by_run(run.id)}
    assert steps["paper_suggestion"].status is ResearchStepStatus.SKIPPED
    assert steps["paper_suggestion"].summary == "No OpenAlex API key is configured (set OPENALEX_API_KEY)."
    assert run.suggested_papers is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `TypeError: GraphDependencies.__init__() got an unexpected keyword argument 'gap_detector'`, `AttributeError: PAPER_SUGGESTION`, and the modified step-order test failing on the missing trailing step.

- [ ] **Step 3: Add `ResearchNode.PAPER_SUGGESTION` and the new state fields**

In `backend/app/agents/planner/state.py`:

Change the `ResearchNode` enum:

```python
class ResearchNode(str, enum.Enum):
    """Canonical node names for the research graph.

    Owned here rather than in `app.modules.research` because the graph
    defines its own topology; the research module persists these values
    as plain strings (`research_steps.node_name`) so adding a node in a
    future sprint never requires a database migration.
    """

    PLANNER = "planner"
    ROUTER = "router"
    ASSET_RETRIEVAL = "asset_retrieval"
    WEB_RESEARCH = "web_research"
    CONTEXT_BUILDER = "context_builder"
    SYNTHESIS = "synthesis"
    #: Milestone 10 step 2: suggests OpenAlex papers for a gap in the
    #: final answer. Not in `RETRIEVAL_NODES` -- the router never
    #: dispatches to it, and it contributes no evidence to `context`.
    PAPER_SUGGESTION = "paper_suggestion"
```

In the same file, add two fields to `ResearchState`, after `topic_relation` and before the "Per-node side output" comment block:

```python
    # --- Paper suggestion node output (Milestone 10 step 2): papers
    # --- OpenAlex suggested to help close a gap in the answer. Never
    # --- read by synthesis -- this node runs strictly after it.
    suggested_papers: list[dict[str, Any]]
    # Whether a configured OpenAlex provider makes `paper_suggestion`
    # worth entering at all. Set once, in `synthesis_node`, so
    # `route_after_synthesis` stays a pure function of state -- the
    # same pattern `web_fallback` already uses.
    paper_suggestion_eligible: bool
```

- [ ] **Step 4: Add `get_paper_provider`, the `GraphDependencies` fields, and `paper_suggestion_node`**

In `backend/app/agents/planner/nodes.py`:

Add to the imports block that already reads `from app.agents.planner.state import (...)` — no change needed there. Add a new import right after the `synthesis` import block (after the `from app.agents.planner.synthesis import (...)` block):

```python
from app.agents.planner.paper_suggestion import (
    GapDetector,
    OpenAlexProvider,
    build_relevance_note,
    build_search_query,
)
```

Immediately after `get_web_provider()` (right before `@dataclass(frozen=True)\nclass GraphDependencies:`), add:

```python
def get_paper_provider() -> OpenAlexProvider | None:
    """A configured OpenAlex provider, or `None` when no key is set.

    `None` is a real, load-bearing value here (unlike `get_web_provider`,
    which always returns something): `route_after_synthesis` uses
    `paper_suggestion_eligible` to skip the `paper_suggestion` node
    entirely when this is `None`, so "no key" is a true graph-level
    skip rather than a node that runs and finds nothing.
    """
    if settings.openalex_api_key is not None:
        return OpenAlexProvider(api_key=settings.openalex_api_key, timeout=settings.openalex_timeout)
    return None
```

In `GraphDependencies`, add two fields after `unsourced_synthesizer`:

```python
    #: OpenAlex search for `paper_suggestion_node`. `None` means
    #: unconfigured -- `route_after_synthesis` never reaches
    #: `paper_suggestion` in that case, so this is only ever read once
    #: it is already non-`None`.
    paper_provider: OpenAlexProvider | None = None
    #: The evidence-gap detector `paper_suggestion_node` runs before
    #: searching OpenAlex. `None` in test fakes that never exercise
    #: this path; a real run gets `GapDetector()` from `build_dependencies`.
    gap_detector: GapDetector | None = None
```

In `build_dependencies`, add two lines to the returned `GraphDependencies(...)`:

```python
        # The same gateway/model configuration the manual `/unsourced`
        # path uses -- and none at all when synthesis is configured offline.
        unsourced_synthesizer=UnsourcedSynthesizer() if settings.synthesis_grounded else None,
        paper_provider=get_paper_provider(),
        gap_detector=GapDetector(),
    )
```

In `synthesis_node`'s returned dict (the `return {"final_answer": answer, ...}` block), add one key right after `"web_fallback": web_fallback,`:

```python
        "web_fallback": web_fallback,
        "paper_suggestion_eligible": dependencies.paper_provider is not None,
```

Update `route_after_synthesis`:

```python
def route_after_synthesis(state: ResearchState) -> str:
    """Loop back through web research once, when synthesis asked for it;
    otherwise fall through to paper suggestion when it is worth trying.

    The web-fallback decision is made in `synthesis_node`, which alone
    knows the grounding outcome and whether the web provider is live;
    `paper_suggestion_eligible` is set there too, for the same reason
    (only `synthesis_node` has the injected dependencies). This only
    reads both, so routing stays a pure function of state.
    """
    if state.get("web_fallback"):
        return ResearchNode.WEB_RESEARCH.value
    if state.get("paper_suggestion_eligible"):
        return ResearchNode.PAPER_SUGGESTION.value
    return SYNTHESIS_DONE
```

Add `paper_suggestion_node` at the end of the "Nodes" section, immediately after `synthesis_node` and before the "Conditional edges" section comment:

```python
async def paper_suggestion_node(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    """Suggest OpenAlex papers that could fill a gap in the final answer.

    Reached only when `route_after_synthesis` found `paper_suggestion_eligible`
    (an OpenAlex key is configured). `final_answer`, `citations`,
    `visualization`, and `equations` are already settled by this point
    and are never read for grounding purposes here, nor written to --
    see `paper_suggestion.py`'s module docstring for why papers found
    here can never become evidence.
    """
    dependencies = _dependencies(config)
    gap_detector = dependencies.gap_detector or GapDetector()
    answer = state.get("final_answer") or ""

    gaps = await gap_detector.detect(
        query=state["query"], answer=answer, grounding_status=state.get("grounding_status", "")
    )

    logger.info(
        "research_node_paper_suggestion",
        run_id=state.get("run_id"),
        gap_count=len(gaps),
    )

    if not gaps:
        return {
            "step": {
                "node": ResearchNode.PAPER_SUGGESTION.value,
                "title": "Suggest supporting papers",
                "summary": "No evidence gaps identified; no papers suggested.",
                "output": {"gap_count": 0, "paper_count": 0},
            }
        }

    if dependencies.paper_provider is None:
        # Only reachable when a node is invoked directly (as some tests
        # do) rather than through `route_after_synthesis`, which already
        # guards this. Handled the same way as "no gaps" rather than
        # asserting, so a direct call degrades gracefully instead of
        # crashing on an internal invariant a caller cannot see.
        return {
            "step": {
                "node": ResearchNode.PAPER_SUGGESTION.value,
                "title": "Suggest supporting papers",
                "summary": "OpenAlex is not configured; no papers suggested.",
                "output": {"gap_count": len(gaps), "paper_count": 0},
            }
        }

    note = build_relevance_note(gaps)
    papers = await dependencies.paper_provider.search(
        query=build_search_query(state["query"], gaps), limit=5
    )
    suggested = [{**paper, "relevance_note": f"{note}. {paper['relevance_note']}"} for paper in papers]

    return {
        "suggested_papers": suggested,
        "step": {
            "node": ResearchNode.PAPER_SUGGESTION.value,
            "title": "Suggest supporting papers",
            "summary": (
                f"Identified {len(gaps)} evidence gap(s); "
                f"suggested {len(suggested)} paper(s) from OpenAlex."
            ),
            "output": {
                "gap_count": len(gaps),
                "paper_count": len(suggested),
                "gaps": [gap.model_dump() for gap in gaps],
            },
        },
    }
```

- [ ] **Step 5: Register the agent**

In `backend/app/agents/planner/registry.py`, add the import (extend the existing `from app.agents.planner.nodes import (...)` block with `paper_suggestion_node`, keeping the list alphabetical):

```python
from app.agents.planner.nodes import (
    asset_retrieval_node,
    context_builder_node,
    paper_suggestion_node,
    planner_node,
    router_node,
    synthesis_node,
    web_research_node,
)
```

Add the registry entry at the end of `AGENT_REGISTRY`, after `ResearchNode.SYNTHESIS.value`:

```python
    ResearchNode.SYNTHESIS.value: NodeSpec(
        name=ResearchNode.SYNTHESIS.value,
        title="Synthesize the deliverable",
        handler=synthesis_node,
        critical=True,
        description="Produces the final answer with structured citations.",
    ),
    ResearchNode.PAPER_SUGGESTION.value: NodeSpec(
        name=ResearchNode.PAPER_SUGGESTION.value,
        title="Suggest supporting papers",
        handler=paper_suggestion_node,
        critical=False,
        description=(
            "Searches OpenAlex for papers that could fill an evidence gap "
            "in the answer. Never contributes evidence to synthesis."
        ),
    ),
}
```

- [ ] **Step 6: Wire the graph edges**

In `backend/app/agents/planner/graph.py`, update the module docstring's topology block:

```python
Topology::

    START
      -> planner
      -> router
           |-- asset_retrieval ---------------------+
           |-- web_research (no knowledge base) ----|--> context_builder
           \\---------------------------------------+
      -> context_builder
      -> synthesis
           |-- evidence insufficient, live web search configured, web not
           |   yet tried --> web_research -> context_builder -> synthesis
           |-- otherwise, OpenAlex configured --> paper_suggestion -> END
           \\-- otherwise --> END
```

Then replace the final conditional-edges block:

```python
    # The web-fallback loop. `route_after_synthesis` returns to web
    # research only once (`web_research_attempted`), so it is bounded.
    # Once that loop settles, it either enters `paper_suggestion` (an
    # OpenAlex key is configured) or ends the run directly.
    builder.add_conditional_edges(
        ResearchNode.SYNTHESIS.value,
        route_after_synthesis,
        {
            ResearchNode.WEB_RESEARCH.value: ResearchNode.WEB_RESEARCH.value,
            ResearchNode.PAPER_SUGGESTION.value: ResearchNode.PAPER_SUGGESTION.value,
            SYNTHESIS_DONE: END,
        },
    )
    builder.add_edge(ResearchNode.PAPER_SUGGESTION.value, END)

    return builder
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_orchestrator.py tests/test_paper_suggestion.py -v`
Expected: all passed. The end-to-end DB test (`test_paper_suggestion_skip_reason_via_the_execution_service`) will still fail here — `run.suggested_papers` and the exact skip-reason text depend on Task 4 (`_skip_reason`, the model column, `_complete`). Confirm it fails only on those, not on graph wiring.

- [ ] **Step 8: Commit**

```bash
git add backend/app/agents/planner/state.py backend/app/agents/planner/nodes.py backend/app/agents/planner/registry.py backend/app/agents/planner/graph.py backend/tests/test_orchestrator.py
git commit -m "feat(paper-suggestions): wire the paper_suggestion node into the research graph

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Persist `suggested_papers` — migration, model, schema, service

**Files:**
- Create: `backend/alembic/versions/c19e4b7d0a52_add_research_run_suggested_papers.py`
- Modify: `backend/app/modules/research/models.py`
- Modify: `backend/app/modules/research/schemas.py`
- Modify: `backend/app/modules/research/service.py`
- Test: `backend/tests/test_orchestrator.py` (the Task 3 DB test now passes)

**Interfaces:**
- Consumes: `ResearchNode.PAPER_SUGGESTION` (Task 3).
- Produces: `ResearchRun.suggested_papers: list[dict[str, Any]] | None`; `ResearchRunRead.suggested_papers: list[dict[str, Any]] | None`; the `paper_suggestion` branch of `service._skip_reason`.

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/c19e4b7d0a52_add_research_run_suggested_papers.py`:

```python
"""add research run suggested papers

One nullable, additive JSONB column on `research_runs` (existing rows
need no backfill): the OpenAlex papers `paper_suggestion_node` found to
help close a gap in the answer. Each entry: openalex_id, title,
authors, year, cited_by_count, landing_url, oa_pdf_url (nullable),
relevance_note. Never used to ground `final_answer`.

Revision ID: c19e4b7d0a52
Revises: a41c7d2e9f05
Create Date: 2026-09-14 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c19e4b7d0a52'
down_revision: Union[str, None] = 'a41c7d2e9f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'research_runs',
        sa.Column('suggested_papers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('research_runs', 'suggested_papers')
```

- [ ] **Step 2: Apply and round-trip the migration**

Run: `docker exec aikdap_backend alembic upgrade head && docker exec aikdap_backend alembic downgrade -1 && docker exec aikdap_backend alembic upgrade head`
Expected: all three succeed.

Run: `docker exec aikdap_backend alembic heads`
Expected: `c19e4b7d0a52 (head)`

- [ ] **Step 3: Add the column to the model**

In `backend/app/modules/research/models.py`, add the column immediately after `equations` (before the `celery_task_id` line):

```python
    # Equations the answer relies on, when the question involved any:
    # `schemas.Equation` entries, each with the backend-sampled `curve`
    # (see `planner.equations`). Null when there were none.
    equations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Papers OpenAlex suggested to help close a gap in the answer
    # (Milestone 10 step 2): `paper_suggestion.SuggestedPaper` entries.
    # Null when the step was skipped (no key) or found no gaps. Never
    # fed into synthesis -- see `agents.planner.paper_suggestion`.
    suggested_papers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 4: Add the field to `ResearchRunRead`**

In `backend/app/modules/research/schemas.py`, add to `ResearchRunRead` immediately after `equations`:

```python
    #: Validated `Equation`s the answer relies on, each with its
    #: backend-sampled `curve` (or null), when the question involved any.
    equations: list[dict[str, Any]] | None = None
    #: OpenAlex papers suggested to help close a gap in the answer, when
    #: any were found. Never used to ground `final_answer` -- see
    #: `agents.planner.paper_suggestion`.
    suggested_papers: list[dict[str, Any]] | None = None
```

`ResearchRunDetail.from_model` needs no change: it builds from `base.model_dump()`, which now includes `suggested_papers`.

- [ ] **Step 5: Persist it in `_complete` and add the skip reason**

In `backend/app/modules/research/service.py`, in `_complete`, add one line after `run.equations = final_state.get("equations") or None`:

```python
        run.equations = final_state.get("equations") or None
        run.suggested_papers = final_state.get("suggested_papers") or None
```

In `_skip_reason`, add a branch before the final `return "Not dispatched by the router for this run."`:

```python
    if node is ResearchNode.PAPER_SUGGESTION:
        return "No OpenAlex API key is configured (set OPENALEX_API_KEY)."
    return "Not dispatched by the router for this run."
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_orchestrator.py tests/test_paper_suggestion.py -v`
Expected: all passed, including `test_paper_suggestion_skip_reason_via_the_execution_service`.

Run: `docker exec aikdap_backend python -m pytest -q`
Expected: no new failures compared with `main`/`feature/persona`, beyond the pre-existing environmental ones (`test_execution_*`, `test_cross_paper`, `test_fallback_grounding`).

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/c19e4b7d0a52_add_research_run_suggested_papers.py backend/app/modules/research/models.py backend/app/modules/research/schemas.py backend/app/modules/research/service.py
git commit -m "feat(paper-suggestions): persist suggested_papers on the research run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend panel

**Files:**
- Modify: `frontend/src/types/api.d.ts` (regenerated)
- Create: `frontend/src/features/research/PaperSuggestionsPanel.tsx`
- Modify: `frontend/src/features/research/ResearchResult.tsx`
- Test: `frontend/src/features/research/PaperSuggestionsPanel.test.tsx`

**Interfaces:**
- Consumes: `ResearchRunDetail.suggested_papers` (Task 4, via regenerated `components["schemas"]["ResearchRunDetail"]`).
- Produces: `SuggestedPaper` (TS interface), `<PaperSuggestionsPanel papers={SuggestedPaper[]} />`.

- [ ] **Step 1: Regenerate the API types**

With the backend running on port 8001 (already up via `docker compose`, or start it: `docker compose up -d backend`):

Run (from `frontend/`): `npm run generate:api`
Then check: `grep -n "suggested_papers" src/types/api.d.ts`
Expected: a `suggested_papers?: ... | null` entry on `ResearchRunRead`.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/features/research/PaperSuggestionsPanel.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PaperSuggestionsPanel, type SuggestedPaper } from "@/features/research/PaperSuggestionsPanel";
import { renderWithProviders } from "@/test/render";

function makePaper(overrides: Partial<SuggestedPaper> = {}): SuggestedPaper {
  return {
    openalex_id: "https://openalex.org/W1",
    title: "Deep Learning for Poultry Disease Detection",
    authors: ["A. Researcher", "B. Scientist"],
    year: 2023,
    cited_by_count: 42,
    landing_url: "https://example.org/paper",
    oa_pdf_url: null,
    relevance_note: "Suggested to help address: missing baseline comparison.",
    ...overrides,
  };
}

describe("PaperSuggestionsPanel", () => {
  it("renders nothing when there are no papers", () => {
    const { container } = renderWithProviders(<PaperSuggestionsPanel papers={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a card per paper, with the publisher-link fallback when there is no OA PDF", () => {
    renderWithProviders(<PaperSuggestionsPanel papers={[makePaper()]} />);

    expect(screen.getByText("Deep Learning for Poultry Disease Detection")).toBeInTheDocument();
    expect(screen.getByText(/A\. Researcher, B\. Scientist/)).toBeInTheDocument();
    expect(screen.getByText(/2023/)).toBeInTheDocument();
    expect(screen.getByText(/42 citations/)).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "Open on publisher site" });
    expect(link).toHaveAttribute("href", "https://example.org/paper");
  });

  it("links to the open-access PDF when one is available", () => {
    renderWithProviders(
      <PaperSuggestionsPanel papers={[makePaper({ oa_pdf_url: "https://example.org/paper.pdf" })]} />,
    );

    const link = screen.getByRole("link", { name: "Open PDF" });
    expect(link).toHaveAttribute("href", "https://example.org/paper.pdf");
  });

  it("renders one card per paper when there are several", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", title: "First paper" }), makePaper({ openalex_id: "W2", title: "Second paper" })]}
      />,
    );

    expect(screen.getByText("First paper")).toBeInTheDocument();
    expect(screen.getByText("Second paper")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run src/features/research/PaperSuggestionsPanel.test.tsx`
Expected: FAIL — cannot find module `@/features/research/PaperSuggestionsPanel`.

- [ ] **Step 4: Create the panel**

Create `frontend/src/features/research/PaperSuggestionsPanel.tsx`:

```tsx
import { ExternalLink } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** One OpenAlex work `paper_suggestion_node` suggested for this run.
 * Mirrors the backend's `paper_suggestion.SuggestedPaper` exactly --
 * see `models.ResearchRun.suggested_papers`. */
export interface SuggestedPaper {
  openalex_id: string;
  title: string;
  authors: string[];
  year: number | null;
  cited_by_count: number;
  landing_url: string;
  oa_pdf_url: string | null;
  relevance_note: string;
}

/** "These papers could strengthen this answer" (spec section 2). Shown
 * only when the run actually has suggestions -- an empty gap search is
 * not a failure worth a card of its own, it is simply nothing to show.
 * No "Add" action yet: that is step 3 (Add & re-run). */
export function PaperSuggestionsPanel({ papers }: { papers: SuggestedPaper[] }) {
  if (papers.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Strengthen this answer</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          These papers could strengthen this answer. Add them to your project.
        </p>
        <ul className="flex flex-col gap-2">
          {papers.map((paper) => (
            <li key={paper.openalex_id} className="flex flex-col gap-1.5 rounded-lg bg-sunken p-3">
              <span className="text-sm font-medium text-foreground">{paper.title}</span>
              <span className="text-xs text-muted-foreground">
                {paper.authors.length > 0 ? paper.authors.join(", ") : "Unknown authors"}
                {paper.year ? ` · ${paper.year}` : ""}
                {" · "}
                {paper.cited_by_count} citation{paper.cited_by_count === 1 ? "" : "s"}
              </span>
              <a
                href={paper.oa_pdf_url ?? paper.landing_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
              >
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
                {paper.oa_pdf_url ? "Open PDF" : "Open on publisher site"}
              </a>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 5: Mount it next to `EvidenceGapPanel` in `ResearchResult`**

In `frontend/src/features/research/ResearchResult.tsx`:

Add the import, after the `EvidenceGapPanel` import line:

```tsx
import { EvidenceGapPanel, GeneralKnowledgeAnswer } from "@/features/research/EvidenceGapPanel";
import { PaperSuggestionsPanel, type SuggestedPaper } from "@/features/research/PaperSuggestionsPanel";
```

Add one line inside the component, right after `const claims = run.claims ?? [];`:

```tsx
  const claims = run.claims ?? [];
  const suggestedPapers = (run.suggested_papers ?? []) as unknown as SuggestedPaper[];
```

In the `insufficient_evidence` branch, add the panel right after `<EvidenceGapPanel ... />`:

```tsx
          <EvidenceGapPanel projectId={run.project_id} query={run.query} runId={run.id} />

          {suggestedPapers.length > 0 && <PaperSuggestionsPanel papers={suggestedPapers} />}
```

In the default (grounded) branch, add the panel right after the `<EvidenceWorkspace ... />` line and before the `citations.length > 0 ? (...)` block:

```tsx
        <EvidenceWorkspace query={run.query} claims={claims} citations={citations} onSelectCitation={openEvidence} />

        {suggestedPapers.length > 0 && <PaperSuggestionsPanel papers={suggestedPapers} />}

        {citations.length > 0 ? (
```

- [ ] **Step 6: Update fixtures and existing snapshots if `tsc` flags them**

`frontend/src/test/fixtures.ts`'s `makeRun` needs no change: `suggested_papers` is optional on `ResearchRunRead` (defaults to `null` server-side), so it is already omittable from the fixture — exactly like `visualization` and `equations` are today.

Run: `npx tsc -b`
Expected: no errors. If any test literal building a full `ResearchRunRead`/`ResearchRunDetail` object (rather than via `makeRun`) is flagged for a missing field, add `suggested_papers: null` there.

- [ ] **Step 7: Run tests to verify they pass**

Run: `npx vitest run src/features/research`
Expected: all passed, including the 4 new `PaperSuggestionsPanel` tests and the existing `ResearchResult.test.tsx` / `EvidenceGapPanel.test.tsx` suites (unaffected: they never set `suggested_papers`, so `suggestedPapers` is always `[]` and the new panel renders nothing there).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/api.d.ts frontend/src/features/research/PaperSuggestionsPanel.tsx frontend/src/features/research/PaperSuggestionsPanel.test.tsx frontend/src/features/research/ResearchResult.tsx
git commit -m "feat(paper-suggestions): show suggested papers in the research result

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Full verification

**Files:** none new.

- [ ] **Step 1: Backend suite**

Run: `docker exec aikdap_backend python -m pytest -q`
Expected: all passed except the pre-existing environmental failures (`test_execution_*`, `test_cross_paper`, `test_fallback_grounding`); no other regressions versus `feature/persona`.

- [ ] **Step 2: Frontend suite and build**

Run (from `frontend/`): `npm test && npx tsc -b && npm run build`
Expected: all tests pass; no type errors; build succeeds.

- [ ] **Step 3: Manual check in the running app**

With `OPENALEX_API_KEY` left unset (the default in this repo's `.env`): start a research run whose knowledge base is thin enough to leave a gap, confirm in `research_steps` (or the run's trace UI) that `paper_suggestion` shows as skipped with "No OpenAlex API key is configured (set OPENALEX_API_KEY)." and that no panel appears in the result. This exercises the real, currently-unconfigured path end to end — a real papers-found path needs a live `OPENALEX_API_KEY`, which is out of scope to provision here.

- [ ] **Step 4: Refresh the knowledge graph**

Run (from repo root): `python -m graphify update .`

- [ ] **Step 5: Commit the graph update if it changed tracked files**

```bash
git status --short graphify-out
```

If `graphify-out/` is tracked and changed, commit it:

```bash
git add graphify-out
git commit -m "chore: update knowledge graph after paper suggestions

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

If it is untracked, leave it.
