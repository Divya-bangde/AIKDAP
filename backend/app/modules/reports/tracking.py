"""Persists a report run's trace (hardening item 1, spec section 4).

The database-backed `NodeExecutionTracker` for the report graph: the
same contract `agents.planner.tracking.instrument` drives for research
runs, writing into the same `research_steps` table -- keyed by the
report `Asset` (`asset_id`) instead of a research run, since a report
has no `research_runs` row.

Unlike `research.service.ResearchStepTracker` it writes no
`agent_messages`: those rows require a `run_id`, and a report node's
whole observable output (summary, counts, timing, error) already lives
on the step itself.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner.tracking import NodeExecutionTracker
from app.agents.reports.registry import REPORT_AGENT_REGISTRY, get_node_spec
from app.modules.research.enums import ResearchStepStatus
from app.modules.research.models import ResearchStep
from app.modules.research.repository import ResearchStepRepository

#: Summary recorded on every node that never ran because an earlier,
#: critical node failed -- the trace shows the whole pipeline, not only
#: the nodes that happened to start.
SKIPPED_SUMMARY = "Not run: an earlier step failed."


def scrub_report_error(exc: BaseException) -> str:
    """The only error text ever persisted for a report -- on the asset
    and on its failed step. Keeps just the exception type name: never
    sensitive, and enough to tell failure modes apart. Matches
    `paper_suggestion.OpenAlexProvider`'s scrubbing discipline."""
    return f"Report generation failed ({type(exc).__name__})."


class ReportStepTracker(NodeExecutionTracker):
    """Writes one `research_steps` row per report node, `running ->
    completed | failed`, committing per node so a later failure never
    erases completed steps.

    Every row carries this run's `attempt` (1, then +1 per retry), so a
    retried report keeps each attempt's trace separately and in order."""

    def __init__(self, session: AsyncSession, asset_id: uuid.UUID, *, attempt: int) -> None:
        self._session = session
        self._asset_id = asset_id
        self._attempt = attempt
        self._steps = ResearchStepRepository(session)
        self._step_index = 0
        # A plain UUID, not an ORM instance: the failure path's rollback
        # expires every loaded object (same reasoning as
        # `research.service.ResearchStepTracker`).
        self._current_step_id: uuid.UUID | None = None
        self.started_nodes: list[str] = []

    async def on_node_start(self, node: str) -> None:
        step = await self._steps.create(
            ResearchStep(
                asset_id=self._asset_id,
                attempt=self._attempt,
                step_index=self._next_index(),
                node_name=node,
                title=get_node_spec(node).title,
                status=ResearchStepStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
        )
        self._current_step_id = step.id
        self.started_nodes.append(node)
        await self._session.commit()

    async def on_node_success(self, node: str, update: dict[str, Any], duration_ms: int) -> None:
        step = await self._current_step()
        if step is None:
            return
        report = update.get("step") or {}
        step.status = ResearchStepStatus.COMPLETED
        step.summary = report.get("summary")
        step.output_payload = report.get("output")
        step.completed_at = datetime.now(timezone.utc)
        step.duration_ms = duration_ms
        await self._session.commit()

    async def on_node_failure(
        self, node: str, error: BaseException, duration_ms: int, critical: bool
    ) -> None:
        # The error may have poisoned the transaction; the `running` row
        # was already committed, so rolling back loses nothing.
        await self._session.rollback()
        step = await self._current_step()
        if step is None:
            return
        step.status = ResearchStepStatus.FAILED
        step.summary = f"Failed: {get_node_spec(node).title}."
        step.error_message = scrub_report_error(error)
        step.completed_at = datetime.now(timezone.utc)
        step.duration_ms = duration_ms
        await self._session.commit()

    async def record_skipped(self) -> None:
        """Record every registered node that never started as `skipped`."""
        for name, spec in REPORT_AGENT_REGISTRY.items():
            if name in self.started_nodes:
                continue
            await self._steps.create(
                ResearchStep(
                    asset_id=self._asset_id,
                    attempt=self._attempt,
                    step_index=self._next_index(),
                    node_name=name,
                    title=spec.title,
                    status=ResearchStepStatus.SKIPPED,
                    summary=SKIPPED_SUMMARY,
                )
            )
        await self._session.commit()

    async def _current_step(self) -> ResearchStep | None:
        if self._current_step_id is None:
            return None
        return await self._session.get(ResearchStep, self._current_step_id)

    def _next_index(self) -> int:
        index = self._step_index
        self._step_index += 1
        return index
