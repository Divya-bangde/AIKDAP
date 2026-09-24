"""Pydantic v2 schemas for the Experiment Plan Engine (Sprint 16 Phase 6).

This module defines a structured, reproducible EXPERIMENT PLAN — not an
executed experiment. Nothing here runs code, trains a model, or produces
observed results; see module docstring in `experiment.py` for the
execution boundary this phase deliberately stops short of.

Reuses `ResearchCertainty` (explicit/strongly_supported/interpreted/
unknown) from `app.modules.research.schemas` rather than inventing a
second confidence scale, and `SourceReference` for provenance so an
experiment variable can point back at the same paper/page/chunk a
cross-paper comparison item would.
"""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.modules.research.schemas import ResearchCertainty, SourceReference


class ExperimentGoal(str, Enum):
    """Mirrors the four research goals this phase must respect (Part M).

    Distinct from `ResearchGoal.type` (free text, e.g. 'reproduce') on
    `ResearchDocumentUnderstanding` -- this is the closed vocabulary an
    experiment plan actually branches its behavior on. `from_free_text`
    maps the free-text goal onto this enum conservatively.
    """

    UNDERSTANDING = "understanding"
    REPRODUCTION = "reproduction"
    EXTENSION = "extension"
    APPLICATION = "application"

    @classmethod
    def from_free_text(cls, text: str | None) -> "ExperimentGoal":
        if not text:
            return cls.UNDERSTANDING
        lowered = text.strip().lower()
        if "reproduc" in lowered:
            return cls.REPRODUCTION
        if "extend" in lowered or "extension" in lowered:
            return cls.EXTENSION
        if "applica" in lowered or "deploy" in lowered:
            return cls.APPLICATION
        # 'understand', 'continue_research', and anything unrecognized
        # default to UNDERSTANDING -- the least prescriptive goal, never
        # guessed toward REPRODUCTION's stricter "no missing values" bar.
        return cls.UNDERSTANDING


class VariableRole(str, Enum):
    """What kind of quantity this is -- never conflated with mutability
    (Part C): a VARIABLE can be fixed, a PARAMETER can be mutable."""

    VARIABLE = "variable"
    PARAMETER = "parameter"
    CONSTANT = "constant"
    DERIVED_QUANTITY = "derived_quantity"
    INPUT = "input"
    OUTPUT = "output"
    UNKNOWN = "unknown"


class MutabilityStatus(str, Enum):
    MUTABLE = "mutable"
    FIXED = "fixed"
    UNKNOWN = "unknown"


class MutabilityEvidenceSource(str, Enum):
    """The evidence hierarchy from Part D, recorded rather than
    discarded once a decision is made, so a user can see *why*."""

    PAPER_STATEMENT = "paper_statement"
    EXPERIMENT_CONTEXT = "experiment_context"
    WORKSPACE_STATE = "workspace_state"
    RESEARCH_UNDERSTANDING = "research_understanding"
    LLM_INTERPRETATION = "llm_interpretation"
    USER_OVERRIDE = "user_override"
    NONE = "none"


class ConstraintSource(str, Enum):
    MATHEMATICAL_CONSTRAINT = "mathematical_constraint"
    PAPER_CONSTRAINT = "paper_constraint"
    USER_CONSTRAINT = "user_constraint"


class OutputKind(str, Enum):
    """Expected and observed values are never the same field (Part Y.6) --
    this tags which one a given `ExperimentOutputValue` is."""

    EXPECTED = "expected"
    OBSERVED = "observed"


class RunPlanStatus(str, Enum):
    PLANNED = "planned"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HypothesisOrigin(str, Enum):
    """Never let an experiment plan present a hypothesis as more settled
    than it is (Part L)."""

    USER_CREATED = "user_created"
    STORED_RESEARCH_HYPOTHESIS = "stored_research_hypothesis"
    AI_GENERATED_UNVALIDATED = "ai_generated_unvalidated"


class ExperimentVariable(BaseModel):
    """One variable/parameter/constant/derived quantity in the plan.

    `mutable` and `role` are independent axes on purpose (Part C) --
    validated together only by `mutable_requires_reason`.
    """

    name: str
    role: VariableRole
    mutable: MutabilityStatus
    mutability_reason: str = Field(
        description="Human-readable explanation of why this mutability was assigned."
    )
    mutability_evidence: MutabilityEvidenceSource = MutabilityEvidenceSource.NONE
    current_value: str | None = None
    unit: str | None = None
    allowed_values: list[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    certainty: ResearchCertainty = ResearchCertainty.UNKNOWN
    source_reference: SourceReference | None = None

    @field_validator("mutability_reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("mutability_reason must not be blank -- see Part D.")
        return value


class ExperimentInput(BaseModel):
    name: str
    value: str
    type: str = Field(description="e.g. 'number', 'string', 'category'")
    source_reference: SourceReference | None = None


class ExperimentOutput(BaseModel):
    name: str
    type: str
    description: str | None = None
    source_reference: SourceReference | None = None


class ExperimentConstraint(BaseModel):
    expression: str
    description: str
    source: ConstraintSource
    source_reference: SourceReference | None = None


class ExperimentOutputValue(BaseModel):
    """A single (output name, value) pair, tagged EXPECTED or OBSERVED.

    Never both on one object -- Part Y.6 forbids mixing them, so a test
    case carries two separate lists instead of one ambiguous dict.
    """

    output_name: str
    value: str
    kind: OutputKind


class ExperimentTestCase(BaseModel):
    id: str
    inputs: dict[str, str]
    expected_outputs: list[ExperimentOutputValue] = Field(default_factory=list)
    observed_outputs: list[ExperimentOutputValue] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    source_reference: SourceReference | None = None

    @field_validator("expected_outputs")
    @classmethod
    def _expected_only(cls, values: list[ExperimentOutputValue]) -> list[ExperimentOutputValue]:
        for v in values:
            if v.kind != OutputKind.EXPECTED:
                raise ValueError("expected_outputs may only contain OutputKind.EXPECTED values.")
        return values

    @field_validator("observed_outputs")
    @classmethod
    def _observed_only(cls, values: list[ExperimentOutputValue]) -> list[ExperimentOutputValue]:
        for v in values:
            if v.kind != OutputKind.OBSERVED:
                raise ValueError("observed_outputs may only contain OutputKind.OBSERVED values.")
        return values


class ExperimentHypothesisRef(BaseModel):
    """A pointer to a hypothesis, with its origin never lost (Part L)."""

    description: str
    origin: HypothesisOrigin
    source_hypothesis_id: str | None = Field(
        default=None, description="ID of the stored ResearchHypothesis, if origin is STORED."
    )
    certainty: ResearchCertainty = ResearchCertainty.UNKNOWN


class ExperimentVariant(BaseModel):
    """BASELINE or a named VARIANT -- overrides on top of the plan's
    variables, never a full copy (Part I/K): a variant only states what
    it changes."""

    id: str
    name: str
    is_baseline: bool = False
    overrides: dict[str, str] = Field(
        default_factory=dict, description="variable name -> overridden value"
    )
    status: RunPlanStatus = RunPlanStatus.PLANNED
    notes: str | None = None


class ExperimentPlanChange(BaseModel):
    """One entry in the plan's version history (Part O) -- deliberately
    flat, not a full diff/patch system."""

    version: int
    changed_at: datetime
    changed_field: str
    old_value: str | None
    new_value: str | None
    reason: str | None = None


class ExperimentPlan(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    objective: str
    goal: ExperimentGoal
    hypothesis: ExperimentHypothesisRef | None = None
    source_expression: str | None = Field(
        default=None,
        description="The raw equation this plan was parsed from (set only by "
        "create_from_equation), e.g. 'Y = (a*X + b) / (c*X + d)'. Sprint 16 Phase "
        "7B.23: the ONE field a plan-to-execution mapping can currently use to "
        "derive an ExecutionOperation (EVALUATE_EXPRESSION) -- a plan with no "
        "source_expression (e.g. created via create_from_understanding) cannot be "
        "mapped to any of the other six catalog operations, since nothing else on "
        "this model identifies which one is intended. Absent on plans persisted "
        "before this field existed; defaults to None, not backfilled.",
    )
    source_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Research/source/citation context ONLY -- the paper(s) this plan "
        "cites or was derived from. Sprint 16 Phase 7B.24: NEVER treated as execution "
        "inputs. See execution_input_asset_ids for the field that actually feeds "
        "ExecutionJob.input_asset_ids.",
    )
    execution_input_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets EXPLICITLY declared as execution inputs (Sprint 16 Phase "
        "7B.24) -- distinct from source_asset_ids. Only assets listed here are ever "
        "mapped onto ExecutionJob.input_asset_ids; never derived from source_asset_ids "
        "or any other field. Validated (owned by the caller, same project) at the "
        "point they are declared (currently only create_from_equation accepts this). "
        "Absent on plans persisted before this field existed; defaults to an empty "
        "list, never backfilled -- an old plan has zero execution inputs, not an "
        "unknown set.",
    )
    variables: list[ExperimentVariable] = Field(default_factory=list)
    inputs: list[ExperimentInput] = Field(default_factory=list)
    outputs: list[ExperimentOutput] = Field(default_factory=list)
    constraints: list[ExperimentConstraint] = Field(default_factory=list)
    test_cases: list[ExperimentTestCase] = Field(default_factory=list)
    variants: list[ExperimentVariant] = Field(default_factory=list)
    version: int = 1
    history: list[ExperimentPlanChange] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# API request/response payloads
# ---------------------------------------------------------------------------


class ExperimentPlanCreateFromEquation(BaseModel):
    project_id: uuid.UUID
    title: str
    expression: str = Field(
        max_length=500,
        description="e.g. 'Y = (a*X + b) / (c*X + d)'. Capped to match the module-level "
        "length guard in app.agents.planner.experiment (Phase 6 security review) -- an "
        "early 422 here is cheaper than reaching the parser.",
    )
    goal: ExperimentGoal = ExperimentGoal.UNDERSTANDING
    source_asset_id: uuid.UUID | None = Field(
        default=None,
        description="The paper this equation was derived from, if any -- research/"
        "citation context only. NOT an execution input; see execution_input_asset_ids.",
    )
    known_inputs: list[str] | None = Field(
        default=None,
        description="Symbol names the caller already knows are inputs (e.g. ['X']). "
        "The equation's math alone cannot tell an input apart from a parameter, so "
        "any symbol not listed here (and not matched to a paper's ResearchVariable) "
        "is classified role=UNKNOWN rather than guessed.",
    )
    execution_input_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets to explicitly declare as execution inputs for this plan "
        "(Sprint 16 Phase 7B.24) -- separate from source_asset_id. Each must be an "
        "asset the caller owns, in this same project; validated here at creation "
        "time, not re-derived from source_asset_id or anything else.",
    )


class ExperimentPlanCreateFromUnderstanding(BaseModel):
    project_id: uuid.UUID
    title: str
    source_asset_id: uuid.UUID
    goal: ExperimentGoal = ExperimentGoal.UNDERSTANDING


class ExperimentPlanUpdateRequest(BaseModel):
    """Every field optional -- only supplied fields are changed, and
    each change is appended to `history` (Part O)."""

    title: str | None = None
    objective: str | None = None
    variables: list[ExperimentVariable] | None = None
    inputs: list[ExperimentInput] | None = None
    outputs: list[ExperimentOutput] | None = None
    constraints: list[ExperimentConstraint] | None = None
    reason: str | None = None
    allow_fixed_variable_change: bool = Field(
        default=False,
        description="Must be explicitly set to change a variable whose "
        "mutable status is FIXED (Part Y.4) -- an accidental overwrite "
        "is rejected by default.",
    )


class ExperimentVariantCreateRequest(BaseModel):
    name: str
    overrides: dict[str, str] = Field(default_factory=dict)
    notes: str | None = None


class ExperimentExecuteRequest(BaseModel):
    """Body of `POST /experiments/{plan_id}/execute` (Sprint 16 Phase
    7B.23) -- names ONE variant to run. No operation, no parameters, no
    input assets: all of that is derived from the plan itself by
    `ExperimentPlanService.request_execution`, never accepted from the
    caller."""

    variant_id: str


class ExperimentSweepRequest(BaseModel):
    """A parameter sweep spec -- turned into planned variants, never
    executed (Part J)."""

    parameter_values: dict[str, list[str]] = Field(
        description="variable name -> list of discrete values to sweep"
    )


class TestCaseImportResponse(BaseModel):
    imported: list[ExperimentTestCase]
    rejected: list[dict[str, str]] = Field(
        default_factory=list, description="rows that failed to parse, with a reason"
    )


class VisualizationSeries(BaseModel):
    """Raw trace data for the frontend to render with Plotly.js -- the
    backend never renders a chart image or asserts causality (Part H/R)."""

    name: str
    x: list[str]
    y: list[float | None]
    kind: str = Field(description="'scatter' | 'line' | 'bar'")


class VisualizationData(BaseModel):
    chart_type: str
    x_label: str
    y_label: str
    series: list[VisualizationSeries]
    note: str | None = Field(
        default=None,
        description="e.g. a trend disclaimer -- never a causal claim (Part H).",
    )
