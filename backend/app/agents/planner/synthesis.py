"""Synthesis strategies: turning retrieved evidence into the deliverable.

Split out of `nodes.py` for the same reason `planner.py` is: a strategy
with real behaviour of its own does not belong beside the thin graph
functions that call it. `nodes.py` imports from here, never the reverse,
so there is one direction of dependency and no cycle.

Two implementations, both satisfying the same `Synthesizer` contract:

- `ExtractiveSynthesizer` — no model involved. Every sentence is lifted
  from a real evidence item, so it cannot hallucinate. Retained as the
  offline path (`SYNTHESIS_GROUNDED=false`) and used by the existing
  Sprint 7/8 tests.
- `GroundedSynthesizer` — Sprint 9E. Sends the question and *only* the
  retrieved evidence to a real model through the AIKDAP LLM gateway,
  then validates what comes back.

The second one is where the grounding guarantees live, and they are
enforced here in the backend rather than requested politely in a
prompt:

1. **Only retrieved evidence is sent.** The model receives the system
   instructions, the question, the objective, and the evidence blocks —
   nothing else. It has no database access, no tools, and no history.
2. **Only supplied evidence can be cited.** Every citation id the model
   returns is checked against the ids that were actually put in its
   prompt. Unknown ids are dropped and recorded, never repaired into
   something plausible.
3. **The model does not grade itself.** `grounding_status` is computed
   from the validated citation set. A model claiming it was grounded
   while citing nothing is recorded as `insufficient_evidence`.

Nothing in this module imports an LLM SDK. Every model call goes
through `app.core.llm.LLMGateway`, which is the only thing in AIKDAP
that imports LiteLLM.
"""

import json
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import get_args

from pydantic import ValidationError

from app.agents.planner.equations import build_equations
from app.agents.planner.prompts import (
    GROUNDED_SYNTHESIS_SYSTEM_PROMPT,
    UNSOURCED_SYNTHESIS_SYSTEM_PROMPT,
    render_grounded_evidence,
    render_grounded_synthesis_prompt,
    render_unsourced_synthesis_prompt,
)
from app.agents.planner.state import Citation, RetrievedDocument
from app.core.config import settings
from app.core.llm import LLMGateway, get_llm_gateway
from app.core.logging.logger import get_logger
from app.modules.research.claim_verification import (
    evidence_state_from_claim_verification,
    verify_categorical_claim,
    verify_numeric_claim,
)
from app.modules.research.enums import ResearchGroundingStatus
from app.modules.research.schemas import (
    GroundedSynthesisResponse,
    SynthesisClaim,
    SynthesisClaimScope,
    SynthesisClaimType,
    TopicRelation,
    UnsourcedSynthesisResponse,
    Visualization,
)

logger = get_logger(__name__)

#: Strips a ```json ... ``` fence, which models emit even when asked for
#: raw JSON. Tolerating the fence is not the same as tolerating an
#: unparseable response — anything still not JSON after this is an error.
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class SynthesisResponseError(Exception):
    """The synthesis model answered with something unusable.

    Raised rather than salvaged: if the response cannot be parsed, the
    mapping from claims to citations is unknown, and an answer whose
    citations cannot be verified is exactly what this sprint exists to
    prevent. The synthesis node is critical, so this fails the run and
    records the reason — it never yields an uncited answer.
    """


@dataclass(frozen=True)
class SynthesisResult:
    """What a synthesizer produced, plus how well it is supported.

    Richer than the `(answer, citations)` tuple this contract used
    before Sprint 9E, because grounding status and the rejected ids are
    findings in their own right: they belong in the run record and the
    Explainable-AI trace, not only in a log line.
    """

    answer: str
    citations: list[Citation]
    grounding_status: ResearchGroundingStatus
    #: Citation ids the model returned that were never supplied to it.
    #: Kept (rather than merely counted) so the trace shows exactly what
    #: was rejected.
    rejected_citation_ids: list[str] = field(default_factory=list)
    #: Claims the model asserted, each run through the real Phase 8.5
    #: deterministic verifier (Sprint 16 Phase 8.7). Serializable dicts,
    #: shaped like `VerifiedClaimRead`, ready to ride alongside
    #: `citations` into `research_runs.citations` JSONB with no schema
    #: change. Empty for the extractive path, which has no model.
    verified_claims: list[dict] = field(default_factory=list)
    #: Every real, supplied `Citation` a verified claim's
    #: `source_reference_ids` resolved to (Sprint 16 Phase 8.8 Part B) --
    #: not merely a copy of `citations` above. A claim is validated
    #: against `supplied` (everything the model was given), while
    #: `citations` is only the model's own top-level cited set; a claim
    #: can legitimately resolve evidence the model never listed at the
    #: top level (the real Phase 8.7 case: an `insufficient_evidence`
    #: answer with zero top-level citations, whose one claim still
    #: resolved real evidence). Never fabricated -- every entry here is
    #: copied verbatim from `supplied`, the same evidence already sent
    #: to the model. `nodes.synthesis_node` unions this into what gets
    #: persisted, so a claim's evidence chip is never backed by nothing.
    claim_referenced_citations: list[Citation] = field(default_factory=list)
    #: How many evidence items were actually placed in the model's
    #: prompt. The upper bound on how many citations can be legitimate.
    evidence_supplied: int = 0
    #: Model metadata, when a model was involved. Absent for the
    #: extractive path.
    model: str | None = None
    provider: str | None = None
    latency_ms: int | None = None
    #: How the answer was obtained (Sprint 9G). A fallback answer is a
    #: perfectly good answer — it went through the same evidence
    #: selection, the same prompt, and the same citation validation as
    #: a primary one — but the trace must never leave it looking
    #: indistinguishable from the model that was actually configured.
    fallback_used: bool = False
    #: The model the gateway started with. Differs from `model` only
    #: when a fallback answered.
    primary_model: str | None = None
    #: Why the primary did not answer. `None` on the happy path.
    primary_error_type: str | None = None
    #: Total provider calls made, retries and fallbacks included.
    llm_attempts: int | None = None
    #: The exact prompt sent, for the audit trail. `None` when no model
    #: was called.
    prompt: str | None = None
    #: A validated chart/diagram spec (`schemas.Visualization`, dumped),
    #: only when the question asked for one and the answer is grounded.
    visualization: dict | None = None
    #: Validated equations (see `planner.equations.build_equations`),
    #: only when the question involved any and the answer is grounded.
    equations: list[dict] = field(default_factory=list)
    #: How the question relates to the subject of the supplied evidence,
    #: as the model judged it. `None` when no model judged it: the
    #: extractive path, a skipped call, or a missing/invalid value.
    topic_relation: TopicRelation | None = None


class Synthesizer(ABC):
    """Contract for producing the final deliverable from evidence.

    Implementations must ground the answer in the supplied evidence and
    return the structured citations they actually relied on — never a
    superset, and never fabricated entries.

    `documents` carries the raw evidence; `citations` is the same
    evidence already ranked, de-duplicated, and keyed by the context
    builder. An implementation cites by `Citation.id` and returns the
    subset it used.

    `warnings` lists the non-critical agents that failed during the
    run. Implementations must surface them in the answer: a deliverable
    built from partial evidence has to say so.
    """

    name: str = "abstract"

    @abstractmethod
    async def synthesize(
        self,
        *,
        query: str,
        objective: str,
        context: str,
        documents: list[RetrievedDocument],
        citations: list[Citation],
        warnings: list[str],
        full_evidence_text: dict[str, str] | None = None,
        conversation: dict[str, str] | None = None,
    ) -> SynthesisResult:
        """Return the answer, the citations it relies on, and how grounded it is.

        `full_evidence_text` (Sprint 16 Phase 8.10 blocker fix) maps a
        citation's `chunk_id` to that chunk's full, untruncated content,
        for implementations that verify claims against evidence text --
        `citation["snippet"]` alone is truncated to `SNIPPET_CHARACTERS`
        for the UI and must never be the sole text a claim is checked
        against. Optional and defaulted so callers with nothing to
        verify (`ExtractiveSynthesizer`, the evaluation harness's direct
        dict-based calls) are unaffected.

        `conversation` is the parent run's `{"query", "answer"}` when this
        run is a follow-up: context for reading the question, never
        evidence. Implementations without a model ignore it.
        """


class ExtractiveSynthesizer(Synthesizer):
    """Builds the deliverable by extracting from the retrieved evidence.

    Extractive rather than generative: every sentence in the answer is
    lifted from a real document and attributed. That makes it honest in
    the absence of an LLM — the output never contains a claim the
    evidence does not support — while producing a genuinely useful
    deliverable rather than a placeholder string.

    Every citation the context builder supplied is returned, including
    simulated ones. Provenance is expressed as metadata on each
    citation (`simulated`, `provider`, `source`) rather than by
    silently dropping items, so a caller can filter on it while the
    trace still shows what the answer was actually built from.
    """

    name = "extractive_v1"

    async def synthesize(
        self,
        *,
        query: str,
        objective: str,
        context: str,
        documents: list[RetrievedDocument],
        citations: list[Citation],
        warnings: list[str],
        full_evidence_text: dict[str, str] | None = None,
        conversation: dict[str, str] | None = None,
    ) -> SynthesisResult:
        """Assemble a cited answer from the supplied citations.

        `full_evidence_text` is unused: this path makes no claims for a
        verifier to check against -- every sentence is a direct excerpt
        of a citation, already attributed to it.
        """
        if not citations:
            answer = (
                f"## Objective\n{objective}\n\n"
                "## Result\nNo evidence could be retrieved for this request. "
                "The project's knowledge base returned no material matching the "
                "query, and no external research source returned results. Upload "
                "and process relevant assets, then re-run this research.\n"
            )
            return SynthesisResult(
                answer=f"{answer}{_warning_section(warnings)}",
                citations=[],
                grounding_status=ResearchGroundingStatus.INSUFFICIENT_EVIDENCE,
            )

        grounded = [item for item in citations if not item["simulated"]]
        simulated = [item for item in citations if item["simulated"]]

        lines = ["## Objective", objective]
        # Incomplete evidence is stated up front, not buried below the
        # findings: a reader must know the answer is partial before
        # reading conclusions drawn from it.
        if warnings:
            lines.extend(["", "## Incomplete evidence"])
            lines.extend(f"- {warning}" for warning in warnings)
        lines.extend(["", "## Key findings"])
        for citation in citations:
            marker = " *(simulated)*" if citation["simulated"] else ""
            lines.append(
                f"- [{citation['id']}] **{citation['title']}**{marker} "
                f"(relevance {citation['score']:.2f}) — "
                f"{_first_sentences(citation['snippet'])}"
            )

        if simulated:
            lines.extend(
                [
                    "",
                    "## Evidence quality",
                    (
                        f"{len(simulated)} of {len(citations)} cited item(s) were "
                        "produced by a simulated research provider and are marked "
                        "`simulated` in the citation metadata. They record what a "
                        "live provider would have been asked for; they are not "
                        "verified sources and must not be treated as evidence."
                    ),
                ]
            )
            if not grounded:
                lines.append(
                    "No grounded evidence was available for this run, so no claim "
                    "below is independently supported."
                )

        lines.extend(["", "## Sources"])
        for citation in citations:
            marker = " *(simulated)*" if citation["simulated"] else ""
            lines.append(
                f"- [{citation['id']}] `{citation['reference']}` — "
                f"{citation['title']} "
                f"(source: {citation['source']}, provider: {citation['provider']})"
                f"{marker}"
            )

        return SynthesisResult(
            answer="\n".join(lines),
            citations=list(citations),
            # Simulated evidence supports nothing: an answer built only
            # from placeholders is not grounded, however well-formed it
            # looks.
            grounding_status=(
                ResearchGroundingStatus.GROUNDED
                if grounded
                else ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
            ),
            evidence_supplied=len(citations),
        )


class GroundedSynthesizer(Synthesizer):
    """Answers the question with a real model, from retrieved evidence only.

    The model is reached through `app.core.llm.LLMGateway` — the single
    LLM entry point in AIKDAP — so the provider, the credential
    handling, the error taxonomy, and the secret scrubbing are the ones
    already built and tested in Sprint 9A. This class contains no
    provider-specific code and no LiteLLM import.

    Simulated evidence is withheld from the model rather than passed
    with a warning label. A placeholder produced by the mock web
    provider contains no facts, so allowing it into the prompt could
    only ever invite an unsupported claim; withholding it also keeps
    the invariant crisp, since the citation set the model is given is
    exactly the set it may cite.
    """

    name = "grounded_llm_v1"

    def __init__(
        self,
        *,
        gateway: LLMGateway | None = None,
        model: str | None = None,
        max_evidence_characters: int | None = None,
    ) -> None:
        # Injected in tests so the gateway boundary itself is exercised
        # (a fake gateway), rather than the gateway being bypassed.
        self._gateway = gateway or get_llm_gateway()
        self._model = model or settings.synthesis_model
        self._max_evidence_characters = (
            max_evidence_characters or settings.synthesis_max_evidence_characters
        )

    @property
    def model(self) -> str:
        """The model this synthesizer asks the gateway for."""
        return self._model

    async def synthesize(
        self,
        *,
        query: str,
        objective: str,
        context: str,
        documents: list[RetrievedDocument],
        citations: list[Citation],
        warnings: list[str],
        full_evidence_text: dict[str, str] | None = None,
        conversation: dict[str, str] | None = None,
    ) -> SynthesisResult:
        """Answer `query` from `citations`, then verify what came back."""
        supplied, withheld_simulated, withheld_budget = self._select_evidence(citations)

        if not supplied:
            # No model call: with nothing to ground in, any generated
            # text would be unsupported by construction. This is the
            # negative acceptance path, and it costs nothing to be
            # certain about.
            logger.info(
                "grounded_synthesis_skipped",
                reason="no_grounded_evidence",
                received_citations=len(citations),
                withheld_simulated=withheld_simulated,
            )
            return SynthesisResult(
                answer=_insufficient_answer(
                    objective=objective,
                    query=query,
                    withheld_simulated=withheld_simulated,
                    warnings=warnings,
                ),
                citations=[],
                grounding_status=ResearchGroundingStatus.INSUFFICIENT_EVIDENCE,
                evidence_supplied=0,
            )

        prompt = render_grounded_synthesis_prompt(
            objective=objective or query,
            query=query,
            evidence=render_grounded_evidence(list(supplied)),
            conversation=conversation,
        )

        logger.info(
            "grounded_synthesis_started",
            model=self._model,
            evidence_supplied=len(supplied),
            withheld_simulated=withheld_simulated,
            withheld_budget=withheld_budget,
        )

        # Any gateway failure propagates. Synthesis is a critical node,
        # so the run is recorded as failed with the real reason — which
        # is honest, and strictly better than completing a run with an
        # answer no model actually produced.
        response = await self._gateway.generate(
            prompt=prompt,
            system_prompt=GROUNDED_SYNTHESIS_SYSTEM_PROMPT,
            model=self._model,
            # Sprint 16 Phase 8.7: json_schema instead of plain
            # json_object, the same pattern Phase 8.2/8.3 already proved
            # on `ResearchDocumentUnderstanding` -- lets `claims` be
            # requested as part of the one existing model call rather
            # than parsed back out of the answer's prose afterward.
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "GroundedSynthesisResponse",
                    "schema": GroundedSynthesisResponse.model_json_schema(),
                },
            },
        )

        (
            answer,
            claimed_ids,
            claimed_status,
            claimed_claims,
            equations,
            topic_relation,
            visualization,
        ) = _parse_response(response.content)
        accepted, rejected = _validate_citation_ids(claimed_ids, supplied)
        status = _grounding_status(
            claimed_status=claimed_status, accepted=accepted, rejected=rejected
        )
        verified_claims, claim_referenced_citations = _verify_claims(
            claimed_claims, supplied, full_evidence_text or {}
        )

        if rejected:
            # Loud on purpose: a model inventing citation ids is the
            # failure mode Phase 8 exists to catch, and it should be
            # visible in the logs, not only in the response payload.
            logger.warning(
                "grounded_synthesis_citations_rejected",
                model=response.model,
                rejected_ids=rejected,
                supplied_ids=[item["id"] for item in supplied],
            )

        logger.info(
            "grounded_synthesis_completed",
            model=response.model,
            provider=response.provider,
            latency_ms=response.latency_ms,
            finish_reason=response.finish_reason,
            total_tokens=response.total_tokens,
            evidence_supplied=len(supplied),
            citations_accepted=len(accepted),
            citations_rejected=len(rejected),
            grounding_status=status.value,
            answer_characters=len(answer),
            # Sprint 9G: which provider actually answered, and whether
            # it was the configured one.
            fallback_used=response.fallback_used,
            primary_model=response.primary_model,
            primary_error_type=response.primary_error_type,
            llm_attempts=response.attempts,
        )

        if verified_claims:
            logger.info(
                "grounded_synthesis_claims_verified",
                claim_count=len(verified_claims),
                verdict_counts=_count_by(verified_claims, "verdict"),
                evidence_state_counts=_count_by(verified_claims, "evidence_state"),
            )

        return SynthesisResult(
            answer=_decorate(
                answer,
                warnings=warnings,
                withheld_simulated=withheld_simulated,
                rejected=rejected,
            ),
            citations=accepted,
            verified_claims=verified_claims,
            claim_referenced_citations=claim_referenced_citations,
            grounding_status=status,
            # A chart drawn from evidence judged insufficient would present
            # exactly the unsupported figures grounding exists to withhold.
            visualization=visualization if status is ResearchGroundingStatus.GROUNDED else None,
            # Same reason: equations are presented as what the evidence says.
            equations=equations if status is ResearchGroundingStatus.GROUNDED else [],
            topic_relation=topic_relation,
            rejected_citation_ids=rejected,
            evidence_supplied=len(supplied),
            model=response.model,
            provider=response.provider,
            latency_ms=response.latency_ms,
            fallback_used=response.fallback_used,
            primary_model=response.primary_model,
            primary_error_type=response.primary_error_type,
            llm_attempts=response.attempts,
            prompt=prompt,
        )

    def _select_evidence(
        self, citations: list[Citation]
    ) -> tuple[list[Citation], int, int]:
        """Choose the evidence to send, within the character budget.

        Returns the items that will be in the prompt plus counts of what
        was withheld and why. The returned list is authoritative for
        everything downstream: only these ids may be cited, so an item
        dropped here can never appear in the final answer's citations.
        """
        grounded = [item for item in citations if not item.get("simulated", True)]
        withheld_simulated = len(citations) - len(grounded)

        supplied: list[Citation] = []
        used = 0
        for citation in grounded:
            cost = len(citation.get("snippet") or "") + len(citation.get("title") or "")
            if supplied and used + cost > self._max_evidence_characters:
                break
            supplied.append(citation)
            used += cost

        return supplied, withheld_simulated, len(grounded) - len(supplied)


class UnsourcedSynthesizer:
    """Answers from the model's own knowledge -- deliberately not evidence-bound.

    Not a `Synthesizer`: that ABC's contract (`documents`, `citations`,
    `context`, `full_evidence_text`) describes grounding in retrieved
    evidence, and this class has none of that to receive. It exists
    beside `ExtractiveSynthesizer`/`GroundedSynthesizer` in this module
    (Sprint 16 Phase 8.13), reached only once an answer could not be
    grounded: by `nodes.synthesis_node` (with `brief=True`) when a run's
    final pass is still `insufficient_evidence`, and by a dedicated
    service call when the user asks from an insufficient-evidence
    result. Never a branch inside `GroundedSynthesizer`, and never
    returned by `get_synthesizer()`.

    The no-fabricated-citations guarantee is structural, not a checked
    rule: `UnsourcedSynthesisResponse` has no `citation_ids` field for
    the model to populate, so `SynthesisResult.citations` below is
    always the literal `[]` -- there is no accepted/rejected split to
    compute, because there is nothing supplied to validate against.
    """

    name = "unsourced_llm_v1"

    def __init__(self, *, gateway: LLMGateway | None = None, model: str | None = None) -> None:
        self._gateway = gateway or get_llm_gateway()
        self._model = model or settings.synthesis_model

    @property
    def model(self) -> str:
        """The model this synthesizer asks the gateway for."""
        return self._model

    async def synthesize(self, *, query: str, brief: bool = False) -> SynthesisResult:
        """Answer `query` from the model's own knowledge alone.

        `brief` is the graph's automatic path: a 2-4 sentence answer,
        returned bare because the caller writes its disclosure line --
        which there depends on why the evidence fell short.
        """
        prompt = render_unsourced_synthesis_prompt(query=query, brief=brief)

        logger.info("unsourced_synthesis_started", model=self._model)

        response = await self._gateway.generate(
            prompt=prompt,
            system_prompt=UNSOURCED_SYNTHESIS_SYSTEM_PROMPT,
            model=self._model,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "UnsourcedSynthesisResponse",
                    "schema": UnsourcedSynthesisResponse.model_json_schema(),
                },
            },
        )

        answer, would_need = _parse_unsourced_response(response.content)

        logger.info(
            "unsourced_synthesis_completed",
            model=response.model,
            provider=response.provider,
            latency_ms=response.latency_ms,
            fallback_used=response.fallback_used,
            primary_model=response.primary_model,
            primary_error_type=response.primary_error_type,
            llm_attempts=response.attempts,
        )

        return SynthesisResult(
            answer=(
                answer if brief else _compose_unsourced_answer(answer=answer, would_need=would_need)
            ),
            # Never derived from the model's response -- there is no
            # `citation_ids` field in `UnsourcedSynthesisResponse` for it
            # to populate, so this is the only value this line could
            # ever hold.
            citations=[],
            grounding_status=ResearchGroundingStatus.UNSOURCED,
            evidence_supplied=0,
            model=response.model,
            provider=response.provider,
            latency_ms=response.latency_ms,
            fallback_used=response.fallback_used,
            primary_model=response.primary_model,
            primary_error_type=response.primary_error_type,
            llm_attempts=response.attempts,
            prompt=prompt,
        )


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


def _parse_response(
    content: str,
) -> tuple[
    str,
    list[str],
    str | None,
    list[SynthesisClaim],
    list[dict],
    TopicRelation | None,
    dict | None,
]:
    """Pull the answer, citation ids, status, claims, equations, topic relation and visual out of JSON.

    Strict about the envelope and lenient about nothing important: a
    response that is not a JSON object with a non-empty `answer` is an
    error, because the alternative is guessing at which claims the
    citations belong to.

    `claims` is lenient by contrast: it is untrusted, additive model
    output (Sprint 16 Phase 8.7), not the load-bearing answer/citation
    mapping above. One malformed claim entry is dropped rather than
    failing a response that otherwise parsed fine -- a schema-enforced
    call can still return a claim missing a required field if the
    provider's json_schema support is imperfect, and that must never
    take down an answer that would otherwise be usable.
    """
    text = (content or "").strip()
    fenced = _JSON_FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    if not text:
        raise SynthesisResponseError("The synthesis model returned an empty response.")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SynthesisResponseError(
            "The synthesis model did not return JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise SynthesisResponseError(
            "The synthesis model returned JSON that is not an object."
        )

    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise SynthesisResponseError(
            "The synthesis model returned no answer text."
        )

    raw_ids = payload.get("citation_ids")
    claimed_ids: list[str] = []
    if isinstance(raw_ids, list):
        # Non-string entries are discarded rather than coerced: an id
        # that is not a string was never a valid id, and stringifying it
        # would manufacture one.
        claimed_ids = [item for item in raw_ids if isinstance(item, str) and item.strip()]

    claimed_status = payload.get("grounding_status")

    raw_claims = payload.get("claims")
    claims: list[SynthesisClaim] = []
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            try:
                claims.append(SynthesisClaim.model_validate(item))
            except ValidationError as exc:
                logger.warning("grounded_synthesis_claim_dropped", reason=str(exc))

    # Lenient like `claims`: a malformed spec is dropped, never allowed to
    # fail an answer that otherwise parsed.
    visualization: dict | None = None
    raw_visualization = payload.get("visualization")
    if isinstance(raw_visualization, dict):
        try:
            visualization = Visualization.model_validate(raw_visualization).model_dump(mode="json")
        except ValidationError as exc:
            logger.warning("grounded_synthesis_visualization_dropped", reason=str(exc))

    # Lenient too: anything but an exact known value is "not judged", which
    # the synthesis node treats as `related` -- the path that still searches.
    raw_relation = payload.get("topic_relation")
    topic_relation: TopicRelation | None = (
        raw_relation if raw_relation in get_args(TopicRelation) else None
    )
    if raw_relation is not None and topic_relation is None:
        logger.warning("grounded_synthesis_topic_relation_dropped", value=repr(raw_relation)[:80])

    return (
        answer.strip(),
        claimed_ids,
        claimed_status if isinstance(claimed_status, str) else None,
        claims,
        build_equations(payload.get("equations")),
        topic_relation,
        visualization,
    )


def _parse_unsourced_response(content: str) -> tuple[str, str]:
    """Pull the answer and the citability requirement out of the model's JSON.

    Strict the same way `_parse_response` is strict about the answer:
    a response that is not a JSON object with a non-empty `answer` is
    unusable. `would_need` is required by the schema but degrades to a
    generic notice rather than failing the whole response -- it is a
    disclosure enhancement, not the load-bearing field `answer` is.
    """
    text = (content or "").strip()
    fenced = _JSON_FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    if not text:
        raise SynthesisResponseError("The synthesis model returned an empty response.")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SynthesisResponseError("The synthesis model did not return JSON.") from exc

    if not isinstance(payload, dict):
        raise SynthesisResponseError("The synthesis model returned JSON that is not an object.")

    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise SynthesisResponseError("The synthesis model returned no answer text.")

    would_need = payload.get("would_need")
    if not isinstance(would_need, str) or not would_need.strip():
        would_need = "a source directly addressing this question has not been identified."

    return answer.strip(), would_need.strip()


def _compose_unsourced_answer(*, answer: str, would_need: str) -> str:
    """Build the full disclosure text, self-sufficient once copied elsewhere.

    The container this renders in on screen is visually distinct on
    purpose, but a container is lost the moment someone copies the text
    into a draft (Sprint 16 Phase 8.13 Part C) -- so the warning is
    written into the text itself, not layered on top of it by the UI.
    """
    return (
        "**Not in your uploaded papers. From general knowledge:**\n\n"
        f"{answer}\n\n"
        "## To make this citable\n"
        f"You would need a source establishing: {would_need}\n"
    )


def _validate_citation_ids(
    claimed_ids: list[str], supplied: list[Citation]
) -> tuple[list[Citation], list[str]]:
    """Resolve claimed ids against the evidence actually sent to the model.

    This is the enforcement point for `FINAL_CITATIONS ⊆
    EVIDENCE_SENT_TO_GEMINI`. An id that was not supplied is rejected
    outright — never fuzzy-matched to the nearest real id, which would
    turn a hallucinated citation into an authoritative-looking one.

    Accepted citations are returned in the order the evidence was
    supplied, not the order the model listed them, so the citation list
    stays deterministic for a given evidence set.
    """
    by_id = {citation["id"]: citation for citation in supplied}
    claimed = list(dict.fromkeys(claimed_ids))  # de-duplicate, keep order

    rejected = [item for item in claimed if item not in by_id]
    accepted_ids = {item for item in claimed if item in by_id}
    accepted = [citation for citation in supplied if citation["id"] in accepted_ids]
    return accepted, rejected


def _grounding_status(
    *, claimed_status: str | None, accepted: list[Citation], rejected: list[str]
) -> ResearchGroundingStatus:
    """Decide how grounded the answer is, from the evidence relationship.

    The model's own `grounding_status` is treated as a claim, not a
    verdict. It can only ever *lower* confidence: a model reporting it
    could not answer is believed, but a model reporting "grounded"
    while citing nothing valid is recorded as `insufficient_evidence`.
    """
    if claimed_status == ResearchGroundingStatus.INSUFFICIENT_EVIDENCE.value:
        return ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    if not accepted:
        return ResearchGroundingStatus.INSUFFICIENT_EVIDENCE
    if rejected:
        return ResearchGroundingStatus.PARTIALLY_GROUNDED
    return ResearchGroundingStatus.GROUNDED


# ---------------------------------------------------------------------------
# Claim verification (Sprint 16 Phase 8.7)
# ---------------------------------------------------------------------------


def _verify_claims(
    claims: list[SynthesisClaim],
    supplied: list[Citation],
    full_evidence_text: dict[str, str] | None = None,
) -> tuple[list[dict], list[Citation]]:
    """Bind each claim to its cited evidence, then run the real Phase 8.5 checks.

    The trust boundary is identical to `_validate_citation_ids` above,
    reused rather than re-derived: a claim's `source_reference_ids` are
    resolved against `supplied` (the evidence actually placed in the
    model's prompt), never against `claimed_ids`/`accepted` at the
    whole-answer level -- a claim could legitimately rely on a supplied
    item the model forgot to also list at the top level, and an id it
    invented for one claim must be rejected the same way an invented
    top-level id already is.

    Verdicts and evidence states come straight from
    `app.modules.research.claim_verification` -- the deterministic
    checks stay the sole authority; nothing here re-implements or
    second-guesses them.

    Verified against `full_evidence_text[chunk_id]` when available
    (Sprint 16 Phase 8.10 blocker fix), falling back to the citation's
    own `snippet` otherwise (non-asset evidence, or a lookup miss).
    `citation["snippet"]` is truncated to `SNIPPET_CHARACTERS` for the
    UI; the model was shown the full chunk, so verification must check
    against the same text the model actually saw, not a ~30%-shorter
    excerpt of it. `snippet` itself is never modified -- callers that
    render it for display are unaffected.

    Returns `(verified_claims, referenced_citations)` -- the second is
    every real `Citation` any claim successfully resolved (Sprint 16
    Phase 8.8 Part B), deduplicated by id, so a caller can guarantee
    every id a claim references is actually retrievable rather than
    silently absent from the top-level citation set.
    """
    full_evidence_text = full_evidence_text or {}
    verified: list[dict] = []
    referenced_by_id: dict[str, Citation] = {}
    for claim in claims:
        accepted, rejected = _validate_citation_ids(claim.source_reference_ids, supplied)
        evidence = [
            (
                item["id"],
                full_evidence_text.get(item.get("chunk_id", ""), item.get("snippet") or ""),
            )
            for item in accepted
        ]
        for item in accepted:
            referenced_by_id.setdefault(item["id"], item)

        if claim.claim_type == SynthesisClaimType.NUMERIC and claim.claimed_value:
            result = verify_numeric_claim(
                claimed_value=claim.claimed_value,
                is_aggregate_claim=(claim.scope == SynthesisClaimScope.AGGREGATE),
                evidence=evidence,
            )
        else:
            result = verify_categorical_claim(claim_text=claim.claim_text, evidence=evidence)

        # `evidence_state_from_claim_verification` only reads
        # `citation_accepted` inside its SUPPORTED branch, which both
        # verifiers above only reach when `evidence` is non-empty --
        # i.e. exactly when `accepted` is non-empty. Safe unconditionally.
        state = evidence_state_from_claim_verification(
            result.verdict,
            citation_accepted=bool(accepted),
            is_primary_source=claim.attributed_to_primary,
        )

        verified.append(
            {
                "kind": "claim",
                "claim_text": claim.claim_text,
                "claim_type": claim.claim_type.value,
                "claimed_value": claim.claimed_value,
                "scope": claim.scope.value if claim.scope else None,
                "source_reference_ids": [item["id"] for item in accepted],
                "unresolved_citation_ids": rejected,
                "attributed_to_primary": claim.attributed_to_primary,
                "verdict": result.verdict.value,
                "evidence_state": state.value,
                "matched_evidence_ids": result.matched_evidence_ids,
                "reason": result.reason,
            }
        )
    return verified, list(referenced_by_id.values())


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    """Small logging helper: how many verified claims landed in each value of `key`."""
    return dict(Counter(item[key] for item in items))


# ---------------------------------------------------------------------------
# Answer decoration
# ---------------------------------------------------------------------------


def _decorate(
    answer: str,
    *,
    warnings: list[str],
    withheld_simulated: int,
    rejected: list[str] | None = None,
) -> str:
    """Append the disclosures the model is not responsible for.

    Degraded sources, withheld placeholder evidence, and rejected
    citation ids are facts about the *run*, not about the evidence, so
    they are added by the backend rather than asked of the model —
    which would have to be trusted to repeat them faithfully.

    Rejected ids matter to a reader, not just to the audit trail: the
    invalid citation is dropped from the citation list, but the model's
    sentence citing it stays in the answer, leaving a marker that
    resolves to nothing. Naming them here means an unsupported claim is
    visibly flagged rather than quietly carrying a dead reference. The
    answer text itself is never edited — rewriting a model's claims to
    look better supported than they are would be its own dishonesty.
    """
    sections = [answer, _warning_section(warnings)]
    if rejected:
        sections.append(
            "\n## Unverified citations\n"
            f"The response cited {', '.join(f'`{item}`' for item in rejected)}, "
            "which {} not supplied as evidence for this run and {} been "
            "rejected. Any statement above relying on {} is not supported by "
            "the retrieved evidence.\n".format(
                "was" if len(rejected) == 1 else "were",
                "has" if len(rejected) == 1 else "have",
                "it" if len(rejected) == 1 else "them",
            )
        )
    if withheld_simulated:
        sections.append(
            f"\n## Evidence quality\n{withheld_simulated} simulated reference(s) "
            "were retrieved but withheld from synthesis: they are placeholders "
            "produced because no live external research provider is configured, "
            "and they contain no facts to ground an answer in. Nothing above "
            "relies on them.\n"
        )
    return "".join(section for section in sections if section)


def _insufficient_answer(
    *, objective: str, query: str, withheld_simulated: int, warnings: list[str]
) -> str:
    """The answer used when there is no grounded evidence to reason from.

    Also what `nodes.synthesis_node` states when a run ends declining to
    answer: deterministic, so it never carries a model's off-target prose.
    """
    body = (
        f"## Objective\n{objective or query}\n\n"
        "## Result\nThe available evidence is insufficient to answer this "
        "question. No evidence retrieved for this run supports an answer, "
        "and no general-knowledge answer was generated in its place.\n\n"
        "Upload and process assets containing the relevant information, then "
        "re-run this research.\n"
    )
    return _decorate(body, warnings=warnings, withheld_simulated=withheld_simulated)


def _warning_section(warnings: list[str]) -> str:
    """Render degraded-mode notices, or nothing when the run was clean."""
    if not warnings:
        return ""
    body = "\n".join(f"- {warning}" for warning in warnings)
    return f"\n## Incomplete evidence\n{body}\n"


def _first_sentences(text: str, *, count: int = 2) -> str:
    """Return the leading sentences of an excerpt, for the findings list."""
    sentences = [part.strip() for part in text.replace("\n", " ").split(". ") if part.strip()]
    if not sentences:
        return text
    selected = ". ".join(sentences[:count]).rstrip(".")
    return f"{selected}."


def get_synthesizer() -> Synthesizer:
    """Return the configured synthesis strategy.

    Grounded synthesis is the default. Turning it off
    (`SYNTHESIS_GROUNDED=false`) selects the extractive path
    explicitly — there is no automatic downgrade when the model is
    unreachable, because silently answering from a different mechanism
    than the one configured is exactly the substitution this project
    forbids. A missing credential surfaces as `LLMConfigurationError`
    from the gateway and fails the run with that reason.
    """
    if settings.synthesis_grounded:
        return GroundedSynthesizer()
    return ExtractiveSynthesizer()
