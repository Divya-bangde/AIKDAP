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
from app.modules.research.enums import ResearchGroundingStatus
from app.modules.research.schemas import GapClassification, GapDetectionResponse, ResearchGap

logger = get_logger(__name__)

#: Outcomes where no project source backs the answer, so a gap is certain.
_UNSUPPORTED_STATUSES = frozenset(
    {ResearchGroundingStatus.UNSOURCED.value, ResearchGroundingStatus.INSUFFICIENT_EVIDENCE.value}
)

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
        # An unsourced or insufficient answer has no supporting source by
        # definition. Asking the model is the wrong test here: a fluent
        # general-knowledge answer reads as complete and comes back with
        # no gaps, which suppressed suggestions exactly when they help most.
        if grounding_status in _UNSUPPORTED_STATUSES:
            return [
                ResearchGap(
                    gap_type="no supporting sources",
                    classification=GapClassification.REQUIRED,
                    description="published research on this question",
                    why_needed="No document in this project supports this answer.",
                    search_intent=query,
                )
            ]

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
                    # OpenAlex rejects `search=` containing `?` or `*` with a
                    # 400: they are wildcard syntax only `search.exact=`
                    # accepts, and a research question almost always ends
                    # in `?`.
                    "search": query.replace("?", " ").replace("*", " "),
                    "per_page": limit,
                    "api_key": self._api_key.get_secret_value(),
                },
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # httpx embeds the full request URL -- including the `api_key`
            # query parameter -- in the message, and the execution-tracking
            # layer persists that string to `research_steps.error_message`
            # and serves it in the run's trace API. Re-raise scrubbed,
            # `from None` so the original (URL-bearing) exception is not
            # chained/logged anywhere.
            raise RuntimeError(f"OpenAlex returned {exc.response.status_code}") from None

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
