"""ORM models for the research workspace.

Three tables, one per concern:

- `research_runs` — the unit of work a user starts and polls.
- `research_steps` — what each graph node did, in execution order.
- `agent_messages` — what each agent said, and the prompt it would have
  sent to a model.

The last two exist for the constitution's Explainable AI requirement:
planning, agent execution, tool usage, aggregation, and execution time
must all be traceable after the fact. They are append-only records of a
run, never mutated once written except to close out timing.

The run stores its own inputs (`include_assets`, `include_web`,
`max_results`) rather than deriving them at execution time, so a stored
run is reproducible and the worker never has to guess what the caller
asked for.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import BaseModel
from app.modules.research.enums import (
    AgentMessageRole,
    ResearchGroundingStatus,
    ResearchRunStatus,
    ResearchStepStatus,
)


def _enum_values(enum_cls: type) -> list[str]:
    """Persist enum members by their `.value`, not their `.name`.

    Same rationale as the identical helper in `app.modules.assets.models`:
    SQLAlchemy stores enum *names* by default, which would silently
    diverge from the lowercase values the API exposes.
    """
    return [member.value for member in enum_cls]


class ResearchRun(BaseModel):
    """One execution of the research workflow inside a project."""

    __tablename__ = "research_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional link to the Task that requested this run. Nullable
    # because a run can be started directly against a project; the
    # tasks module's docstring reserves this association for exactly
    # this purpose. SET NULL rather than CASCADE: archiving a task must
    # not erase the audit trail of work already performed.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The completed run this one follows up on ("explain that briefly").
    # Its question and answer reach this run's synthesis prompt as
    # conversation context -- never as citable evidence. SET NULL for the
    # same reason as `task_id`: deleting a parent must not erase a
    # follow-up's own audit trail.
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ResearchRunStatus] = mapped_column(
        SQLEnum(
            ResearchRunStatus,
            name="research_run_status_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ResearchRunStatus.PENDING,
        index=True,
    )

    # Execution inputs, stored so the run is reproducible.
    include_assets: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_web: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    # Planner and synthesis output.
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A list of structured citation objects (see
    # `app.agents.planner.state.Citation`): reference, title, source,
    # provider, snippet, score, and whether the evidence is simulated.
    # Stored as JSONB, so carrying structure rather than bare reference
    # strings needs no schema change.
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    # How well the answer is supported by the evidence that reached the
    # synthesizer. A column rather than a key inside `citations`,
    # because it is a property of the run and needs to be queryable
    # ("show me every run that could not be answered"), which a value
    # buried in a JSONB list is not. Nullable: a run that fails before
    # synthesis has no grounding to report, and null says exactly that
    # rather than implying a verdict.
    grounding_status: Mapped[ResearchGroundingStatus | None] = mapped_column(
        SQLEnum(
            ResearchGroundingStatus,
            name="research_grounding_status_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # A chart or diagram synthesis produced because the question asked
    # for one: `{"kind": "chart2d" | "chart3d" | "diagram", "title", ...}`
    # with Plotly `data`/`layout` for charts or `mermaid` source for
    # diagrams. Validated by `schemas.Visualization` before storage; null
    # when none was requested or the model's spec failed validation.
    visualization: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Equations the answer relies on, when the question involved any:
    # `schemas.Equation` entries, each with the backend-sampled `curve`
    # (see `planner.equations`). Null when there were none.
    equations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Papers OpenAlex suggested to help close a gap in the answer
    # (Milestone 10 step 2): `paper_suggestion.SuggestedPaper` entries.
    # Null when the step was skipped (no key) or found no gaps. Never
    # fed into synthesis -- see `agents.planner.paper_suggestion`.
    suggested_papers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # The Celery task actually carrying out this run, so a run can be
    # correlated with the worker logs that executed it.
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ResearchStep(BaseModel):
    """One graph node's execution within a run."""

    __tablename__ = "research_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Plain string, not an enum: the graph owns its node names
    # (`app.agents.planner.state.ResearchNode`) and adding one must not
    # require a database migration.
    node_name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[ResearchStepStatus] = mapped_column(
        SQLEnum(
            ResearchStepStatus,
            name="research_step_status_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ResearchStepStatus.PENDING,
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AgentMessage(BaseModel):
    """One transcript entry produced by an agent during a run.

    Note on the `metadata` column: the bare attribute name `metadata` is
    reserved on SQLAlchemy's `DeclarativeBase`, so the Python attribute
    is `message_metadata` while the column keeps the domain name —
    identical to the treatment of `Asset.asset_metadata`.
    `schemas.AgentMessageRead.from_model` bridges it back for the API.
    """

    __tablename__ = "agent_messages"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_steps.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    role: Mapped[AgentMessageRole] = mapped_column(
        SQLEnum(
            AgentMessageRole,
            name="agent_message_role_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
