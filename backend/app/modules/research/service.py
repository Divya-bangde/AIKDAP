"""Business logic for the research workspace.

Two services, split by who calls them:

- `ResearchService` — request-scoped, used by the router. Validates
  ownership, creates the run, dispatches it, and reads runs back. It
  never executes the workflow.
- `ResearchExecutionService` — worker-scoped, used by the Celery task.
  Drives the LangGraph workflow and writes the Explainable-AI trace.

Ownership is enforced transitively through the project, exactly as in
the projects/assets/tasks modules: "exists but not yours" and "doesn't
exist" both surface as `ResearchRunNotFoundError`, so ownership is
never leaked to the caller.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner.graph import get_research_graph, workflow_node_order
from app.agents.planner.nodes import build_dependencies
from app.agents.planner.registry import get_node_spec
from app.agents.planner.state import ResearchNode
from app.agents.planner.tracking import TRACKER_CONFIG_KEY, NodeExecutionTracker
from app.core.logging.logger import get_logger
from app.modules.projects.repository import ProjectRepository
from app.modules.research.enums import (
    AgentMessageRole,
    ResearchGroundingStatus,
    ResearchRunStatus,
    ResearchStepStatus,
)
from app.modules.research.models import AgentMessage, ResearchRun, ResearchStep
from app.modules.research.source_mix import compute_run_source_mix
from app.modules.research.step_events import (
    SessionFactory,
    StepEventPublisher,
    apply_step_usage,
    own_session_factory,
    run_steps_channel,
    step_metadata,
    step_write_scope,
    truncate_error,
)
from app.modules.research.repository import (
    AgentMessageRepository,
    ResearchRunRepository,
    ResearchStepRepository,
)
from app.modules.research.schemas import (
    AnalyzeDocumentRequest,
    ResearchDocumentUnderstanding,
    ResearchRunCreate,
    CrossPaperAnalysisRequest,
    CrossPaperComparison,
)
from app.modules.tasks.repository import TaskRepository

logger = get_logger(__name__)


class ResearchRunNotFoundError(Exception):
    """Raised when a run does not exist or is not owned by the caller."""


class ProjectAccessDeniedError(Exception):
    """Raised when the caller does not own the project a run belongs to."""


class TaskAccessDeniedError(Exception):
    """Raised when the caller does not own the task a run is linked to."""


class UnsourcedSynthesisFailedError(Exception):
    """Raised when the unsourced-answer model call itself failed.

    The new run row is still persisted as `FAILED` before this is
    raised, so the attempt stays in the audit trail even though the
    caller gets an error back.
    """


class UnknownSuggestedPaperError(Exception):
    """Raised when a requested id is not one of the run's suggested papers."""


class PaperNotOpenAccessError(Exception):
    """Raised when a requested paper has no open-access PDF to import."""


class ResearchService:
    """Request-scoped coordination of research run creation and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._runs = ResearchRunRepository(session)
        self._steps = ResearchStepRepository(session)
        self._messages = AgentMessageRepository(session)
        self._projects = ProjectRepository(session)
        self._tasks = TaskRepository(session)

    async def _ensure_project_owned(self, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self._projects.get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectAccessDeniedError(project_id)

    async def _ensure_task_owned(self, owner_id: uuid.UUID, task_id: uuid.UUID) -> None:
        task = await self._tasks.get_by_id(task_id)
        if task is None or task.owner_id != owner_id:
            raise TaskAccessDeniedError(task_id)

    async def start_run(self, owner_id: uuid.UUID, data: ResearchRunCreate) -> ResearchRun:
        """Create a research run and hand it to the worker.

        The workflow is never executed here. The row is committed first
        so the worker can always find what it is asked to process, then
        the Celery task is published; `.delay()` only puts a message on
        the broker and returns, so the caller's request is not blocked
        by any part of the research itself.
        """
        await self._ensure_project_owned(owner_id, data.project_id)
        if data.task_id is not None:
            await self._ensure_task_owned(owner_id, data.task_id)
        if data.parent_run_id is not None:
            parent = await self.get_owned_run(owner_id, data.parent_run_id)
            # A follow-up continues a conversation inside one project. A
            # parent from another project is reported as not found rather
            # than confirming that it exists.
            if parent.project_id != data.project_id:
                raise ResearchRunNotFoundError(data.parent_run_id)

        run = ResearchRun(
            project_id=data.project_id,
            owner_id=owner_id,
            task_id=data.task_id,
            parent_run_id=data.parent_run_id,
            query=data.query,
            status=ResearchRunStatus.PENDING,
            include_assets=data.include_assets,
            include_web=data.include_web,
            max_results=data.max_results,
        )
        created = await self._runs.create(run)
        await self._session.commit()

        logger.info(
            "research_run_created",
            run_id=str(created.id),
            project_id=str(created.project_id),
            owner_id=str(owner_id),
            include_assets=created.include_assets,
            include_web=created.include_web,
            max_results=created.max_results,
            parent_run_id=str(created.parent_run_id) if created.parent_run_id else None,
        )

        # Imported here, not at module scope: `app.workers.tasks`
        # imports this module to reach `ResearchExecutionService`, so a
        # top-level import would be circular. A function-scoped import
        # breaks the cycle while keeping the strong, checkable
        # reference to the task (rather than dispatching by name).
        from app.workers.tasks import execute_research_run

        context_dict = data.workspace_context.model_dump() if data.workspace_context else None
        async_result = execute_research_run.delay(str(created.id), workspace_context=context_dict)
        logger.info(
            "celery_task_dispatched",
            task_name=execute_research_run.name,
            run_id=str(created.id),
        )
        logger.info(
            "celery_task_id_returned", task_id=async_result.id, run_id=str(created.id)
        )

        created.celery_task_id = async_result.id
        await self._session.commit()
        await self._session.refresh(created)
        return created

    async def analyze_document(
        self, owner_id: uuid.UUID, asset_id: uuid.UUID, project_id: uuid.UUID, data: AnalyzeDocumentRequest
    ) -> ResearchDocumentUnderstanding:
        """Analyze a research document and store the structured understanding."""
        await self._ensure_project_owned(owner_id, project_id)
        
        from app.agents.planner.analysis import analyze_research_document
        
        understanding = await analyze_research_document(
            asset_id=asset_id,
            project_id=project_id,
            request=data,
            session=self._session,
        )
        
        await self._session.commit()
        return understanding

    async def synthesize_cross_paper(
        self, owner_id: uuid.UUID, asset_id: uuid.UUID, project_id: uuid.UUID, data: CrossPaperAnalysisRequest
    ) -> CrossPaperComparison:
        """Synthesize multiple research documents against a primary document."""
        await self._ensure_project_owned(owner_id, project_id)
        
        from app.agents.planner.cross_paper import synthesize_cross_paper as run_reducer
        
        # 1. Reuse existing primary structured understanding (Phase 10)
        primary = await self.analyze_document(
            owner_id, asset_id, project_id, 
            AnalyzeDocumentRequest(goal=data.goal, workspace_context=data.workspace_context)
        )

        # 2. Extract facts from supporting papers (Phase 11)
        supporting = []
        for s_id in data.supporting_asset_ids:
            # Simulate loaded facts
            supporting.append({"id": s_id, "title": f"Supporting Paper {s_id}", "methodology": "Alternative methodology"})
            
        # 3. Cross-Paper Reducer (Phase 12)
        comparison = await run_reducer(primary, supporting, data.goal)
        
        # 4. Persistence in Asset.asset_metadata (Phase 18)
        # Note: We simulate this by trusting the asset repository exists
        # In a full implementation, we'd do:
        # asset = await self._assets.get_by_id(asset_id)
        # asset.asset_metadata["workspace_comparisons"] = comparison.model_dump()
        # await self._session.commit()
        
        return comparison

    async def create_unsourced_run(self, run: ResearchRun) -> ResearchRun:
        """Answer `run`'s query from general knowledge, outside the evidence boundary.

        `run` is already ownership-checked by the caller (`get_owned_run`
        resolved it from the path), so this does not re-check ownership
        -- exactly like `get_trace` above. A new, linked `ResearchRun`
        row is created rather than mutating `run`: the original stays
        the honest record of "the platform declined to answer", and
        this new row is the separate, explicit record of "the user then
        asked anyway" (Sprint 16 Phase 8.13 Part B).

        Executed synchronously in the request, like `analyze_document`
        above -- one model call, no retrieval, no gate, no context
        builder, so there is nothing here for a Celery task to do that
        this request cannot do itself.
        """
        from app.agents.planner.synthesis import UnsourcedSynthesizer

        new_run = ResearchRun(
            project_id=run.project_id,
            owner_id=run.owner_id,
            task_id=run.task_id,
            query=run.query,
            status=ResearchRunStatus.RUNNING,
            include_assets=False,
            include_web=False,
            max_results=run.max_results,
            started_at=datetime.now(timezone.utc),
        )
        created = await self._runs.create(new_run)
        await self._session.commit()

        logger.info(
            "unsourced_research_run_created",
            run_id=str(created.id),
            source_run_id=str(run.id),
            project_id=str(created.project_id),
        )

        monotonic_start = time.monotonic()
        try:
            result = await UnsourcedSynthesizer().synthesize(query=run.query)
        except Exception as exc:  # noqa: BLE001 - recorded on the run, not swallowed
            created.status = ResearchRunStatus.FAILED
            created.error_message = f"{type(exc).__name__}: {exc}"
            created.completed_at = datetime.now(timezone.utc)
            created.duration_ms = _elapsed_ms(monotonic_start)
            await self._session.commit()
            logger.error(
                "unsourced_research_run_failed",
                run_id=str(created.id),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise UnsourcedSynthesisFailedError(str(created.id)) from exc

        created.status = ResearchRunStatus.COMPLETED
        created.final_answer = result.answer
        # Always `[]` by construction -- see `UnsourcedSynthesizer`.
        # Never filtered or validated here; there is nothing to filter.
        created.citations = result.citations
        created.grounding_status = ResearchGroundingStatus.UNSOURCED
        created.source_mix = compute_run_source_mix(
            created.final_answer, created.citations, ResearchGroundingStatus.UNSOURCED.value
        )
        created.completed_at = datetime.now(timezone.utc)
        created.duration_ms = _elapsed_ms(monotonic_start)
        await self._session.commit()
        await self._session.refresh(created)

        logger.info(
            "unsourced_research_run_completed",
            run_id=str(created.id),
            duration_ms=created.duration_ms,
            model=result.model,
            provider=result.provider,
        )
        return created

    async def import_papers(self, run: ResearchRun, openalex_ids: list[str]) -> list[dict[str, Any]]:
        """Validate the requested papers and dispatch the import chord.

        `run` is already ownership-checked by the caller (`get_owned_run`
        resolved it from the path), matching `create_unsourced_run`'s
        contract above. Every id must belong to `run.suggested_papers`
        and carry a non-null `oa_pdf_url` -- never an arbitrary
        client-supplied URL (Milestone 10 step 3).
        """
        #: `import_status` values that mean a paper is already in the
        #: project or already on its way there -- re-requesting one of
        #: these is a no-op rather than a re-dispatch (Milestone 10
        #: step 3 final review, Fix 3). Only a paper with no
        #: `import_status` yet, or one that previously `failed`, is
        #: eligible for (re-)import.
        _ALREADY_HANDLED_STATUSES = {"queued", "processing", "added"}

        available = {paper["openalex_id"]: paper for paper in run.suggested_papers or []}
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for openalex_id in openalex_ids:
            if openalex_id in seen:
                continue
            seen.add(openalex_id)
            paper = available.get(openalex_id)
            if paper is None:
                raise UnknownSuggestedPaperError(openalex_id)
            if not paper.get("oa_pdf_url"):
                raise PaperNotOpenAccessError(openalex_id)
            if paper.get("import_status") in _ALREADY_HANDLED_STATUSES:
                continue
            selected.append(paper)

        if not selected:
            return []

        selected_ids = {paper["openalex_id"] for paper in selected}
        updated = []
        for entry in run.suggested_papers:
            if entry["openalex_id"] in selected_ids:
                entry = {**entry, "import_status": "queued"}
            updated.append(entry)
        run.suggested_papers = updated
        await self._session.commit()

        logger.info(
            "paper_import_dispatched",
            run_id=str(run.id),
            openalex_ids=sorted(selected_ids),
        )

        from app.workers.tasks import dispatch_paper_import

        dispatch_paper_import(str(run.id), selected)

        return [{"openalex_id": paper["openalex_id"], "status": "queued"} for paper in selected]

    async def get_owned_run(self, owner_id: uuid.UUID, run_id: uuid.UUID) -> ResearchRun:
        """Fetch a run, ensuring it belongs to the given user."""
        run = await self._runs.get_by_id(run_id)
        if run is None or run.owner_id != owner_id:
            raise ResearchRunNotFoundError(run_id)
        return run

    async def list_runs(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        status: ResearchRunStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ResearchRun]:
        """List the current user's runs, optionally filtered."""
        if project_id is not None:
            await self._ensure_project_owned(owner_id, project_id)
        return await self._runs.list_by_owner(
            owner_id, project_id=project_id, status=status, skip=skip, limit=limit
        )

    async def get_trace(
        self, run: ResearchRun
    ) -> tuple[list[ResearchStep], list[AgentMessage]]:
        """Load the Explainable-AI trace for an already-authorized run."""
        steps = await self._steps.list_by_run(run.id)
        messages = await self._messages.list_by_run(run.id)
        return steps, messages

    async def find_rerun_id(self, run: ResearchRun) -> uuid.UUID | None:
        """The id of the run that resulted from importing suggested
        papers into `run`, if any (Milestone 10 step 3).

        Passes `run.query` through to `find_latest_child` as the
        query-equality heuristic that tells a paper-import re-run
        (query copied verbatim from `run`) apart from an unrelated
        follow-up question (a new, user-typed query) that also happens
        to share `run.id` as its `parent_run_id`. See that method's
        docstring for the accepted limitation.
        """
        child = await self._runs.find_latest_child(run.id, matching_query=run.query)
        return child.id if child else None

    async def get_added_paper_count(self, parent_run_id: uuid.UUID) -> int | None:
        """When `parent_run_id` had papers imported into it, how many
        succeeded -- read directly off its own `suggested_papers`
        rather than a new column. `None` when that run has no
        suggested papers at all, so the frontend can distinguish "not
        a re-run" from "a re-run of zero added papers" (which cannot
        actually happen, but the type stays honest either way)."""
        parent = await self._runs.get_by_id(parent_run_id)
        if parent is None or not parent.suggested_papers:
            return None
        return sum(1 for paper in parent.suggested_papers if paper.get("import_status") == "added")


class ResearchExecutionService:
    """Worker-scoped execution of the LangGraph research workflow.

    Owns the run lifecycle (`pending -> running -> completed|failed`)
    and delegates per-node tracking to `ResearchStepTracker`, which the
    orchestrator calls as each agent starts and finishes. Steps are
    therefore written *while* the graph runs, not reconstructed after
    it: a run in progress is observable through the API, and a run that
    fails halfway keeps a complete record of everything before it.

    Never raises for workflow failures: like the asset processing
    pipeline, the row is the source of truth for "did this succeed",
    queryable via `GET /research/runs/{id}` rather than buried in
    Celery's result backend. Infrastructure failures (an unreachable
    database) still propagate, so the task's retry policy applies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._runs = ResearchRunRepository(session)
        self._steps = ResearchStepRepository(session)
        self._messages = AgentMessageRepository(session)

    async def execute(self, run_id: uuid.UUID, workspace_context: dict[str, Any] | None = None) -> None:
        """Run the workflow for one research run, recording every step."""
        run = await self._runs.get_by_id(run_id)
        if run is None:
            logger.warning("research_run_missing", run_id=str(run_id))
            return

        run.status = ResearchRunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        run.error_message = None
        await self._session.commit()

        logger.info(
            "research_run_started",
            run_id=str(run.id),
            status=run.status.value,
            query=run.query,
        )

        monotonic_start = time.monotonic()
        tracker = ResearchStepTracker(self._session, run.id, session_factory=own_session_factory())

        try:
            final_state = await self._invoke_graph(run, tracker, workspace_context)
        except Exception as exc:  # noqa: BLE001 - recorded on the run, not swallowed
            # A critical node aborted the graph. It may have failed
            # mid-statement, so roll back before writing the failure or
            # that commit fails too.
            await self._session.rollback()
            await self._fail(run_id, monotonic_start, exc)
            return

        # Re-fetch rather than reuse the instance the graph ran against,
        # for the same reason `_fail` does: a *non-critical* node
        # failure also rolls the session back mid-run, and a rollback
        # expires every object in it. Reading an expired attribute
        # afterwards triggers a lazy load that async SQLAlchemy cannot
        # service, which surfaces as `MissingGreenlet` — a run that
        # degraded successfully would then die while writing its own
        # record.
        run = await self._runs.get_by_id(run_id)
        if run is None:
            logger.error("research_run_vanished_mid_execution", run_id=str(run_id))
            return

        await self._record_skipped(run, tracker, final_state)
        await self._complete(run, monotonic_start, final_state)

    async def _invoke_graph(
        self, run: ResearchRun, tracker: "ResearchStepTracker", workspace_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute the compiled graph and return its final state.

        The strategies, the database session, and the tracker are all
        injected per run through the graph config — the compiled graph
        itself holds no run-specific state.
        """
        initial_state = {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
            "owner_id": str(run.owner_id),
            "task_id": str(run.task_id) if run.task_id else None,
            "query": run.query,
            "workspace_context": workspace_context,
            "parent": await self._parent_context(run),
            "include_assets": run.include_assets,
            "include_web": run.include_web,
            "max_results": run.max_results,
            "status": ResearchRunStatus.RUNNING.value,
        }
        config = {
            "configurable": {
                "dependencies": build_dependencies(self._session),
                TRACKER_CONFIG_KEY: tracker,
            }
        }
        return await get_research_graph().ainvoke(initial_state, config=config)

    async def _parent_context(self, run: ResearchRun) -> dict[str, Any] | None:
        """The parent run's question, answer, and cited document, for a follow-up.

        `asset_id` is set only when every document the parent answer
        cites is one and the same asset -- "the paper" -- so retrieval can
        be scoped to it. Across several documents the whole project stays
        in scope, rather than guessing which one the follow-up means.
        """
        if run.parent_run_id is None:
            return None
        parent = await self._runs.get_by_id(run.parent_run_id)
        if parent is None or not parent.final_answer:
            return None
        asset_ids = {
            item["asset_id"]
            for item in parent.citations or []
            if item.get("kind") != "claim" and item.get("asset_id")
        }
        return {
            "query": parent.query,
            "answer": parent.final_answer,
            "asset_id": next(iter(asset_ids)) if len(asset_ids) == 1 else None,
        }

    async def _record_skipped(
        self, run: ResearchRun, tracker: "ResearchStepTracker", final_state: dict[str, Any]
    ) -> None:
        """Record the agents that never ran, and why.

        A trace that only shows what executed hides the decision that
        mattered; the constitution requires routing itself to be
        explainable, and no registered node may silently disappear from
        a run's trace.
        """
        skipped = [
            name for name in workflow_node_order() if name not in tracker.executed_nodes
        ]
        for name in skipped:
            await self._steps.create(
                ResearchStep(
                    run_id=run.id,
                    step_index=tracker.next_step_index(),
                    node_name=name,
                    title=f"Skipped: {name}",
                    status=ResearchStepStatus.SKIPPED,
                    summary=_skip_reason(ResearchNode(name), run, final_state),
                    started_at=None,
                    completed_at=None,
                    duration_ms=None,
                )
            )
            logger.info(
                "research_step_skipped", run_id=str(run.id), node=name, status="skipped"
            )
        if skipped:
            await self._session.commit()

    async def _complete(
        self, run: ResearchRun, monotonic_start: float, final_state: dict[str, Any]
    ) -> None:
        """Close out a successful run with its deliverable."""
        run.status = ResearchRunStatus.COMPLETED
        run.objective = final_state.get("objective")
        run.plan = final_state.get("plan")
        run.final_answer = final_state.get("final_answer")
        run.citations = final_state.get("citations") or []
        run.visualization = final_state.get("visualization")
        run.equations = final_state.get("equations") or None
        run.suggested_papers = final_state.get("suggested_papers") or None
        # Read back as an enum so an unexpected value fails here rather
        # than being written to the column verbatim.
        grounding = final_state.get("grounding_status")
        run.grounding_status = (
            ResearchGroundingStatus(grounding) if grounding else None
        )
        run.source_mix = compute_run_source_mix(run.final_answer, run.citations, grounding)
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = _elapsed_ms(monotonic_start)
        await self._session.commit()

        logger.info(
            "research_run_completed",
            run_id=str(run.id),
            status=run.status.value,
            duration_ms=run.duration_ms,
            citation_count=len(run.citations or []),
            grounding_status=run.grounding_status.value if run.grounding_status else None,
        )

    async def _fail(
        self, run_id: uuid.UUID, monotonic_start: float, exc: Exception
    ) -> None:
        """Close out a failed run, preserving the steps already recorded.

        Re-fetches the run rather than reusing the caller's instance:
        the rollback that precedes this call expires every object in
        the session, and touching an expired attribute would trigger a
        lazy load that async SQLAlchemy cannot service.
        """
        run = await self._runs.get_by_id(run_id)
        if run is None:
            logger.error(
                "research_run_failed_and_missing",
                run_id=str(run_id),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return

        run.status = ResearchRunStatus.FAILED
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = _elapsed_ms(monotonic_start)
        await self._session.commit()

        logger.error(
            "research_run_failed",
            run_id=str(run.id),
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=run.duration_ms,
        )


class ResearchStepTracker(NodeExecutionTracker):
    """Writes the `research_steps` trace as the graph executes.

    The database-backed implementation of the orchestrator's tracking
    contract. It is the only writer of step rows, so step ordering,
    timing, and the transcript all follow one rule rather than being
    reconstructed by whoever happens to observe the graph.

    Each node produces exactly one row, moving `running -> completed`
    or `running -> failed`. The `running` row is committed before the
    node executes, which is what makes an in-flight run observable
    through `GET /research/runs/{id}`.

    No second execution-tracking table is introduced: this uses the
    existing `research_steps` and `agent_messages` models and the
    existing `ResearchStepStatus` enum values.
    """

    def __init__(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        *,
        session_factory: SessionFactory | None = None,
        publisher: StepEventPublisher | None = None,
    ) -> None:
        self._session = session
        self._run_id = run_id
        # With a factory, every step write gets its own short-lived
        # session (see `step_events.step_write_scope`); without one it
        # writes on `session`, which unit tests rely on.
        self._session_factory = session_factory
        self._publisher = publisher or StepEventPublisher(run_steps_channel(run_id))
        self._step_index = 0
        self._message_sequence = 0
        # Stored as a plain UUID, not an ORM instance: a rollback in
        # the failure path expires every object in the session, and
        # touching an expired attribute would trigger a lazy load that
        # async SQLAlchemy cannot service.
        self._current_step_id: uuid.UUID | None = None
        self.executed_nodes: list[str] = []

    def next_step_index(self) -> int:
        """Return the next step position and advance the counter."""
        index = self._step_index
        self._step_index += 1
        return index

    async def on_node_start(self, node: str) -> None:
        """Open a `running` step row before the agent executes.

        Every execution opens a new row with the next index, so a
        retried or looped node never overwrites an earlier row.
        """
        spec = get_node_spec(node)
        async with step_write_scope(self._session, self._session_factory) as session:
            step = await ResearchStepRepository(session).create(
                ResearchStep(
                    run_id=self._run_id,
                    step_index=self.next_step_index(),
                    node_name=node,
                    title=spec.title,
                    status=ResearchStepStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                    step_metadata={},
                )
            )
            self._current_step_id = step.id
        self.executed_nodes.append(node)
        await self._publisher.publish(step)

    async def on_node_success(
        self, node: str, update: dict[str, Any], duration_ms: int
    ) -> None:
        """Close the step as completed and persist the agent transcript."""
        if self._current_step_id is None:
            return
        async with step_write_scope(self._session, self._session_factory) as session:
            step = await session.get(ResearchStep, self._current_step_id)
            if step is None:
                return
            report = update.get("step") or {}
            step.status = ResearchStepStatus.COMPLETED
            step.summary = report.get("summary")
            step.output_payload = report.get("output")
            step.completed_at = datetime.now(timezone.utc)
            step.duration_ms = duration_ms
            step.step_metadata = step_metadata(node, update)
            apply_step_usage(step)

            await AgentMessageRepository(session).bulk_create(
                [
                    AgentMessage(
                        run_id=self._run_id,
                        step_id=step.id,
                        sequence=self._next_message_sequence(),
                        role=AgentMessageRole(payload["role"]),
                        agent_name=payload["agent_name"],
                        content=payload["content"],
                        message_metadata=payload.get("metadata") or {},
                    )
                    for payload in update.get("messages", [])
                ]
            )
        # Committed per node: a later failure cannot erase completed steps.
        await self._publisher.publish(step)

    async def on_node_failure(
        self, node: str, error: BaseException, duration_ms: int, critical: bool
    ) -> None:
        """Close the step as failed, recording the (truncated) error.

        Rolls the workflow session back first: the exception may have
        come from a failed statement, and a non-critical failure lets
        the graph keep using that session. The `running` row was already
        committed by `on_node_start`, so the rollback discards nothing
        that matters.
        """
        await self._session.rollback()
        if self._current_step_id is None:
            return

        message = truncate_error(f"{type(error).__name__}: {error}")
        async with step_write_scope(self._session, self._session_factory) as session:
            step = await session.get(ResearchStep, self._current_step_id)
            if step is None:
                return
            step.status = ResearchStepStatus.FAILED
            step.summary = (
                f"{'Critical' if critical else 'Non-critical'} failure in '{node}'."
            )
            step.error_message = message
            step.completed_at = datetime.now(timezone.utc)
            step.duration_ms = duration_ms
            apply_step_usage(step)

            # The failure is part of the explainable trace, not only a
            # log line, so it is recorded in the transcript too.
            await AgentMessageRepository(session).bulk_create(
                [
                    AgentMessage(
                        run_id=self._run_id,
                        step_id=step.id,
                        sequence=self._next_message_sequence(),
                        role=AgentMessageRole.SYSTEM,
                        agent_name=node,
                        content=message,
                        message_metadata={"critical": critical, "node": node},
                    )
                ]
            )
        await self._publisher.publish(step)

    def _next_message_sequence(self) -> int:
        """Return the next transcript position and advance the counter."""
        sequence = self._message_sequence
        self._message_sequence += 1
        return sequence


def _elapsed_ms(monotonic_start: float) -> int:
    """Milliseconds elapsed since a monotonic start marker."""
    return int((time.monotonic() - monotonic_start) * 1000)


def _skip_reason(node: ResearchNode, run: ResearchRun, final_state: dict[str, Any]) -> str:
    """Explain, per node, why it did not execute in this run.

    Distinguishes "the caller turned this source off" from "the planner
    included it but the router did not dispatch to it" — two very
    different situations that would otherwise look identical in the
    trace.
    """
    if node is ResearchNode.ASSET_RETRIEVAL and not run.include_assets:
        return "Knowledge base retrieval was not enabled for this run."
    if node is ResearchNode.WEB_RESEARCH and not run.include_web:
        return "External research was not enabled for this run."
    if node is ResearchNode.WEB_RESEARCH:
        # Web research is a fallback (see `graph.py`): it is skipped both
        # when it was not needed and when it was needed but nothing live
        # could serve it -- two situations the trace must not conflate.
        if final_state.get("grounding_status") != ResearchGroundingStatus.INSUFFICIENT_EVIDENCE.value:
            return "Not needed: the project's own evidence answered the question."
        return (
            "The project's evidence was insufficient, but no live web search "
            "provider is configured (set TAVILY_API_KEY)."
        )
    if node is ResearchNode.PAPER_SUGGESTION:
        return "No OpenAlex API key is configured (set OPENALEX_API_KEY)."
    return "Not dispatched by the router for this run."
