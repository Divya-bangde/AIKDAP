"""Pydantic v2 request/response schemas for the research module.

`plan`, `objective`, `final_answer`, `citations`, and every timing field
are read-only: they are produced by the workflow, never supplied by the
caller. The only thing a client sends is what to research and where to
look.
"""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.research.enums import (
    AgentMessageRole,
    ResearchGroundingStatus,
    ResearchRunStatus,
    ResearchStepStatus,
)
from app.modules.research.models import AgentMessage, ResearchRun

# `ClaimSupport` (claim_verification.py) and `EvidenceState`
# (evidence_state.py) are not imported here even though `VerifiedClaimRead`
# below mirrors their values exactly: both of those modules import FROM
# this one (`ResearchCertainty`, `ComparisonRelationship`), so this file
# is upstream of them -- importing back would be circular. The `Literal`
# aliases below name the identical values as the single source of
# truth, not a second taxonomy.
ClaimVerdictValue = Literal["supported", "contradicted", "insufficient_evidence", "unverifiable"]
EvidenceStateValue = Literal["verified", "derived", "supporting", "unknown", "contradicted"]


class ResearchWorkspaceContext(BaseModel):
    """Explicit application state used to safely resolve ambiguous queries."""
    
    active_entity: str | None = Field(
        default=None, description="The currently selected company or entity in the UI."
    )
    active_asset_id: uuid.UUID | None = Field(
        default=None, description="The currently selected document ID in the UI."
    )
    active_research_document_id: uuid.UUID | None = Field(
        default=None, description="The currently selected research paper ID."
    )


class ResearchRunCreate(BaseModel):
    """Payload for starting a research run."""

    project_id: uuid.UUID
    query: str = Field(min_length=3, max_length=20_000)
    # Optional association with an existing task, validated for
    # ownership like the project is.
    task_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Ask a follow-up on this completed run (same project). Its question "
            "and answer frame the new one; evidence is still retrieved fresh."
        ),
    )
    include_assets: bool = Field(
        default=True, description="Search the project's own knowledge base."
    )
    include_web: bool = Field(
        default=True, description=(
            "Search the web (Tavily) only when the project's own evidence is "
            "insufficient. Simulated when TAVILY_API_KEY is not configured."
        )
    )
    max_results: int = Field(default=5, ge=1, le=20)
    
    workspace_context: ResearchWorkspaceContext | None = Field(
        default=None, description="Explicit UI state for resolving ambiguous queries."
    )


class ResearchRunAccepted(BaseModel):
    """Immediate `201` response: the run exists, execution has been queued.

    Deliberately small. The workflow has not started when this is
    returned, so anything it produces would be a lie at this point —
    poll `GET /research/runs/{run_id}` for results.
    """

    run_id: uuid.UUID
    status: ResearchRunStatus
    project_id: uuid.UUID
    query: str
    created_at: datetime

    @classmethod
    def from_model(cls, run: ResearchRun) -> "ResearchRunAccepted":
        """Build the acceptance response from the freshly created run."""
        return cls(
            run_id=run.id,
            status=run.status,
            project_id=run.project_id,
            query=run.query,
            created_at=run.created_at,
        )


# ---------------------------------------------------------------------------
# Grounded synthesis claims (Sprint 16 Phase 8.7)
# ---------------------------------------------------------------------------
#
# Phase 8.6 found that parsing claims back out of the model's free-text
# markdown answer is the bottleneck (formatting, paraphrase,
# segmentation all break a hand-written parser). The fix is not a
# better parser: `GroundedSynthesizer` already asks the model for
# structured JSON, so the model is asked to emit its claims as part of
# that same JSON instead of a second pass over its own prose.


class SynthesisClaimType(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"


class SynthesisClaimScope(str, Enum):
    AGGREGATE = "aggregate"
    COMPONENT = "component"


class SynthesisClaim(BaseModel):
    """One factual claim, exactly as the model returned it.

    Untrusted model output, not a verified fact: `claim_type` and
    `scope` are the model's own classification of what it just said,
    and `source_reference_ids` are citation ids it says back it --
    schema-valid does not mean supported. `synthesis._verify_claims`
    is what actually checks each of these against the evidence the
    model was given, using the real Phase 8.5 deterministic checks.
    """

    claim_text: str
    claim_type: SynthesisClaimType
    claimed_value: str | None = None
    scope: SynthesisClaimScope | None = None
    source_reference_ids: list[str] = Field(default_factory=list)
    #: Whether the model attributes this claim to the primary paper
    #: (`True`) rather than a supporting one. Always `True` in the
    #: single-project grounded-synthesis path, which has no
    #: primary/supporting concept of its own -- carried here so the
    #: field exists when a future cross-paper-aware caller needs it.
    #: Primary/supporting contamination stays a separate check
    #: (`detect_primary_supporting_contamination`), not folded in here.
    attributed_to_primary: bool = True


class VisualizationKind(str, Enum):
    """What kind of visual a synthesis answer carries."""

    CHART2D = "chart2d"
    CHART3D = "chart3d"
    DIAGRAM = "diagram"


class Visualization(BaseModel):
    """A chart or diagram the synthesis model produced because the
    question asked for one.

    Charts carry Plotly traces (`data`) and `layout`, rendered by the
    frontend as-is; diagrams carry Mermaid source. Validated so a stored
    spec always has something to draw -- a spec that doesn't is dropped by
    `synthesis._parse_response`, never stored half-formed.
    """

    kind: VisualizationKind
    title: str = ""
    data: list[dict[str, Any]] = Field(default_factory=list)
    layout: dict[str, Any] = Field(default_factory=dict)
    mermaid: str | None = None

    @model_validator(mode="after")
    def _has_content_for_kind(self) -> Self:
        if self.kind is VisualizationKind.DIAGRAM:
            if not (self.mermaid and self.mermaid.strip()):
                raise ValueError("a diagram needs Mermaid source")
        elif not self.data:
            raise ValueError("a chart needs at least one Plotly trace")
        return self


class EquationVariable(BaseModel):
    """One symbol of an `Equation`: what it means, its unit, and the value
    and slider range the interactive card starts from.

    `role="constant"` (G, c) is shown fixed, never as a slider.
    `illustrative` is true unless the evidence states the value: a range
    the model picked is never passed off as a measured one. A missing or
    unusable range is replaced by one around `value`, and marked
    illustrative.
    """

    name: str = ""
    unit: str = ""
    role: Literal["input", "constant"] = "input"
    value: float | None = Field(default=None, allow_inf_nan=False)
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)
    illustrative: bool = True

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_name(cls, data: Any) -> Any:
        # Runs stored before variables carried values, and a small model
        # that writes only the meaning, give a plain string.
        return {"name": data} if isinstance(data, str) else data

    @model_validator(mode="after")
    def _usable_range(self) -> Self:
        if self.value is None:
            self.value, self.illustrative = 1.0, True
        if self.min is None or self.max is None or not self.min <= self.value <= self.max or self.min == self.max:
            span = abs(self.value) or 1.0
            self.min, self.max, self.illustrative = self.value - span, self.value + span, True
        return self


class Equation(BaseModel):
    """An equation a synthesis answer relies on: LaTeX for display, what
    each symbol means, and -- only when it can be written as plain math --
    an `expression` the backend can parse and rearrange.

    `expression` is optional because many real equations (integrals,
    tensors, derivatives) display fine but cannot be computed; those are
    still shown, just without sliders or a chart.

    `expressions` is the system form: two or three equations sharing
    symbols (e.g. `["x + y = 5", "x - y = 1"]`), mutually exclusive with
    `expression` -- `planner.equations.build_equations` solves every
    combination of input symbols sized to the equation count instead of
    rearranging a single one."""

    label: str = ""
    latex: str = Field(min_length=1)
    expression: str | None = None
    expressions: list[str] | None = None
    variables: dict[str, EquationVariable] = Field(default_factory=dict)


#: How a question relates to the subject of the evidence it was asked
#: against -- not whether that evidence answers it (`grounding_status`).
#: Routes an insufficient answer: an off-topic question is never sent to
#: web search, and is answered briefly from general knowledge instead.
TopicRelation = Literal["on_topic", "related", "off_topic"]


class GroundedSynthesisResponse(BaseModel):
    """The full JSON-schema-enforced envelope `GroundedSynthesizer` requests.

    Passed to the gateway as `response_format={"type": "json_schema", ...}`
    -- the same pattern Phase 8.2/8.3 already proved on
    `ResearchDocumentUnderstanding`, not a new mechanism.
    """

    answer: str
    citation_ids: list[str] = Field(default_factory=list)
    grounding_status: str | None = None
    topic_relation: TopicRelation | None = None
    claims: list[SynthesisClaim] = Field(default_factory=list)
    equations: list[Equation] = Field(default_factory=list)
    visualization: Visualization | None = None


class UnsourcedSynthesisResponse(BaseModel):
    """The JSON-schema-enforced envelope `UnsourcedSynthesizer` requests.

    Deliberately has no `citation_ids` field at all -- unlike
    `GroundedSynthesisResponse`, there is no evidence in this prompt for
    the model to cite against, so the schema itself gives it nothing to
    invent a citation into (Sprint 16 Phase 8.13).
    """

    answer: str
    would_need: str = Field(
        description="What a source would need to establish for this answer to become a citable claim."
    )


class VerifiedClaimRead(BaseModel):
    """One claim after real, deterministic verification -- what the
    Claim Evidence Panel renders.

    Persisted inside `research_runs.citations` JSONB, alongside
    ordinary citation objects, tagged `kind="claim"` so
    `ResearchRunDetail.from_model` can split the two back apart at read
    time without a schema migration.
    """

    kind: Literal["claim"] = "claim"
    claim_text: str
    claim_type: SynthesisClaimType
    claimed_value: str | None = None
    scope: SynthesisClaimScope | None = None
    #: Citation ids the model gave for this claim that were actually
    #: supplied to it -- the only ones the verifier checked against.
    source_reference_ids: list[str] = Field(default_factory=list)
    #: Ids the model gave for this claim that were never supplied at
    #: all (rejected the same way top-level citation ids are). A claim
    #: can carry both a valid and an invented id at once.
    unresolved_citation_ids: list[str] = Field(default_factory=list)
    attributed_to_primary: bool = True
    #: `ClaimSupport` value (claim_verification.py) -- see
    #: `ClaimVerdictValue`'s comment for why this is a `Literal`
    #: rather than the enum type itself.
    verdict: ClaimVerdictValue
    #: `EvidenceState` value (evidence_state.py).
    evidence_state: EvidenceStateValue
    matched_evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class ResearchRunRead(BaseModel):
    """Full state of a research run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    task_id: uuid.UUID | None
    parent_run_id: uuid.UUID | None = None
    query: str
    status: ResearchRunStatus
    include_assets: bool
    include_web: bool
    max_results: int
    objective: str | None
    plan: dict[str, Any] | None
    final_answer: str | None
    citations: list[dict[str, Any]] | None
    #: How well `final_answer` is supported by the cited evidence.
    #: `None` when the run never reached synthesis. Distinct from
    #: `status`: a run can be `completed` and
    #: `insufficient_evidence` at the same time, which is the honest
    #: answer to an unanswerable question.
    grounding_status: ResearchGroundingStatus | None
    #: A validated `Visualization` spec, when the question asked for one.
    visualization: dict[str, Any] | None = None
    #: Validated `Equation`s the answer relies on, each with its
    #: backend-sampled `curve` (or null), when the question involved any.
    equations: list[dict[str, Any]] | None = None
    error_message: str | None
    celery_task_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    created_at: datetime
    updated_at: datetime


class ResearchStepRead(BaseModel):
    """One node's execution record within a run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    step_index: int
    node_name: str
    title: str
    status: ResearchStepStatus
    summary: str | None
    output_payload: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    created_at: datetime


class AgentMessageRead(BaseModel):
    """One agent transcript entry within a run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    step_id: uuid.UUID | None
    sequence: int
    role: AgentMessageRole
    agent_name: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_model(cls, message: AgentMessage) -> "AgentMessageRead":
        """Build the response from an ORM row.

        Bridges the `message_metadata` attribute back to the `metadata`
        field the API exposes (see the note in `models.AgentMessage`).
        """
        return cls(
            id=message.id,
            run_id=message.run_id,
            step_id=message.step_id,
            sequence=message.sequence,
            role=message.role,
            agent_name=message.agent_name,
            content=message.content,
            metadata=message.message_metadata,
            created_at=message.created_at,
        )


class ResearchRunDetail(ResearchRunRead):
    """A run together with its full Explainable-AI trace."""

    steps: list[ResearchStepRead] = Field(default_factory=list)
    messages: list[AgentMessageRead] = Field(default_factory=list)
    #: Verified claims, split out of `citations` (see `from_model`) so a
    #: client never has to distinguish claim entries from ordinary
    #: citation entries itself.
    claims: list[VerifiedClaimRead] = Field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        run: ResearchRun,
        *,
        steps: list[ResearchStepRead],
        messages: list[AgentMessageRead],
    ) -> "ResearchRunDetail":
        """Build the detail response, splitting claims out of `citations`.

        `run.citations` is one JSONB array carrying both ordinary
        citation dicts and claim dicts (`kind="claim"`) -- see
        `nodes.synthesis_node`. This is the one place that split
        happens, so every other reader of `ResearchRunDetail.citations`
        keeps seeing exactly the citation shape it always has.
        """
        base = ResearchRunRead.model_validate(run)
        raw = base.citations or []
        claims = [VerifiedClaimRead.model_validate(item) for item in raw if item.get("kind") == "claim"]
        citations = [item for item in raw if item.get("kind") != "claim"]
        return cls(
            **{**base.model_dump(), "citations": citations},
            steps=steps,
            messages=messages,
            claims=claims,
        )


# ---------------------------------------------------------------------------
# Research Document Understanding (Sprint 16)
# ---------------------------------------------------------------------------

class ResearchCertainty(str, Enum):
    EXPLICIT = "explicit"
    STRONGLY_SUPPORTED = "strongly_supported"
    INTERPRETED = "interpreted"
    UNKNOWN = "unknown"

class GapClassification(str, Enum):
    REQUIRED = "required"
    HELPFUL = "helpful"
    OPTIONAL = "optional"
    AMBIGUOUS = "ambiguous"

class SufficiencyStatus(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIALLY_SUFFICIENT = "partially_sufficient"
    INSUFFICIENT = "insufficient"
    AMBIGUOUS = "ambiguous"

class ResearchVariable(BaseModel):
    name: str
    role: str
    description: str | None = None
    unit: str | None = None
    source_evidence: str | None = None
    confidence: ResearchCertainty

class ResearchEquation(BaseModel):
    expression: str
    variables: list[str]
    constants: list[str]
    source_evidence: str | None = None
    confidence: ResearchCertainty

class ResearchGap(BaseModel):
    gap_type: str
    classification: GapClassification
    description: str
    why_needed: str
    search_intent: str | None = None

class ResearchConflict(BaseModel):
    description: str
    sources: list[str]

class ResearchGoal(BaseModel):
    type: str = Field(description="e.g. 'reproduce', 'understand', 'continue_research'")
    description: str

class ResearchDocumentUnderstanding(BaseModel):
    title: str
    abstract: str | None = None
    domain: str | None = None
    problem_statement: str | None = None
    motivation: str | None = None
    objectives: list[str] = Field(default_factory=list)
    research_questions: list[str] = Field(default_factory=list)
    methodology: str | None = None
    dataset: str | None = None
    preprocessing: str | None = None
    models: list[str] = Field(default_factory=list)
    algorithms: list[str] = Field(default_factory=list)
    equations: list[ResearchEquation] = Field(default_factory=list)
    variables: list[ResearchVariable] = Field(default_factory=list)
    experimental_setup: str | None = None
    evaluation_metrics: list[str] = Field(default_factory=list)
    results: str | None = None
    conclusions: str | None = None
    limitations: list[str] = Field(default_factory=list)
    future_work: list[str] = Field(default_factory=list)
    explicit_assumptions: list[str] = Field(default_factory=list)
    missing_information: list[ResearchGap] = Field(default_factory=list)
    conflicts: list[ResearchConflict] = Field(default_factory=list)
    sufficiency: SufficiencyStatus
    sufficiency_reason: str

class AnalyzeDocumentRequest(BaseModel):
    goal: ResearchGoal
    workspace_context: ResearchWorkspaceContext | None = None

class CrossPaperAnalysisRequest(BaseModel):
    supporting_asset_ids: list[uuid.UUID]
    goal: ResearchGoal
    workspace_context: ResearchWorkspaceContext | None = None

# ---------------------------------------------------------------------------
# Cross-Paper Comparison (Sprint 16 Phase 5)
# ---------------------------------------------------------------------------

class ComparisonRelationship(str, Enum):
    SUPPORTS = "SUPPORTS"
    EXTENDS = "EXTENDS"
    CONTRADICTS = "CONTRADICTS"
    DIFFERS = "DIFFERS"
    COMPLEMENTS = "COMPLEMENTS"
    UNRELATED = "UNRELATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

class GapResolutionState(str, Enum):
    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    NOT_RESOLVED = "NOT_RESOLVED"
    CONTRADICTED = "CONTRADICTED"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"

class HypothesisStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"

class SourceReference(BaseModel):
    paper_id: uuid.UUID
    paper_title: str
    page: int | None = None
    chunk_index: int | None = None
    evidence_scope: str = "FULL_TEXT"  # e.g., "FULL_TEXT", "ABSTRACT_ONLY"

class ComparisonItem(BaseModel):
    topic: str
    primary_claim: str
    supporting_claims: list[str]
    relationship: ComparisonRelationship
    evidence: str
    certainty: ResearchCertainty
    source_references: list[SourceReference]

class GapResolution(BaseModel):
    gap_id: str | None = None  # Reference to a ResearchGap identifier or index
    gap_description: str
    supporting_paper_ids: list[uuid.UUID]
    resolution_state: GapResolutionState
    explanation: str
    evidence: str
    unresolved_portion: str | None = None

class ResearchHypothesis(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    description: str
    supporting_sources: list[SourceReference]
    rationale: str
    certainty: ResearchCertainty
    status: HypothesisStatus = HypothesisStatus.PROPOSED

class CrossPaperComparison(BaseModel):
    primary_paper_id: uuid.UUID
    supporting_paper_ids: list[uuid.UUID]
    comparison_items: list[ComparisonItem]
    gap_resolutions: list[GapResolution]
    hypotheses: list[ResearchHypothesis]
    source_provenance: str = "Cross-Paper Reducer"

