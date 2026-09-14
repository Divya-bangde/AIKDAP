"""HTTP routes for the research workspace, scoped to the authenticated user.

`POST /research/run` is asynchronous by contract: it validates the
request, creates the run, dispatches it to the Celery worker, and
returns `202`-style acceptance semantics with a `201` status (the run
resource genuinely was created). No part of the workflow executes
inside the request.

The two `GET` routes are what make that contract usable — without a way
to read a run back, an endpoint that only ever returns "pending" would
be inert. `GET /research/runs/{id}` returns the full Explainable-AI
trace: the plan, every node's execution record, and the agent
transcript.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status

from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.execution.models import ExecutionJob
from app.modules.execution.schemas import ExecutionJobRead
from app.modules.research.dependencies import (
    get_experiment_service,
    get_owned_run,
    get_research_service,
)
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.experiment_schemas import (
    ExperimentExecuteRequest,
    ExperimentPlan,
    ExperimentPlanCreateFromEquation,
    ExperimentPlanCreateFromUnderstanding,
    ExperimentPlanUpdateRequest,
    ExperimentSweepRequest,
    ExperimentVariantCreateRequest,
    TestCaseImportResponse,
    VisualizationData,
)
from app.modules.research.experiment_service import (
    ExperimentPlanAccessDeniedError,
    ExperimentPlanNotFoundError,
    ExperimentPlanService,
    ExperimentPlanValidationError,
)
from app.modules.research.models import ResearchRun
from app.modules.research.schemas import (
    AgentMessageRead,
    AnalyzeDocumentRequest,
    ResearchDocumentUnderstanding,
    ResearchRunAccepted,
    ResearchRunCreate,
    ResearchRunDetail,
    ResearchRunRead,
    ResearchStepRead,
    CrossPaperComparison,
    CrossPaperAnalysisRequest,
    PaperImportAccepted,
    PaperImportRequest,
)
from app.modules.research.service import (
    ProjectAccessDeniedError,
    ResearchRunNotFoundError,
    ResearchService,
    TaskAccessDeniedError,
    UnsourcedSynthesisFailedError,
    PaperNotOpenAccessError,
    UnknownSuggestedPaperError,
)

router = APIRouter(prefix="/research", tags=["Research"])

_PROJECT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Project not found."
)
_TASK_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Task not found."
)


@router.post("/run", response_model=ResearchRunAccepted, status_code=status.HTTP_201_CREATED)
async def start_research_run(
    data: ResearchRunCreate,
    current_user: User = Depends(get_current_user),
    service: ResearchService = Depends(get_research_service),
) -> ResearchRunAccepted:
    """Start a research run and return immediately with its id and status.

    The LangGraph workflow runs in the Celery worker; poll
    `GET /research/runs/{run_id}` for the plan, trace, and final answer.
    """
    try:
        run = await service.start_run(current_user.id, data)
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except TaskAccessDeniedError as exc:
        raise _TASK_NOT_FOUND from exc
    except ResearchRunNotFoundError as exc:
        # Only `parent_run_id` can raise this when starting a run.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent research run not found."
        ) from exc
    return ResearchRunAccepted.from_model(run)


@router.get("/runs", response_model=list[ResearchRunRead])
async def list_research_runs(
    project_id: uuid.UUID | None = Query(default=None),
    status_filter: ResearchRunStatus | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    service: ResearchService = Depends(get_research_service),
) -> list[ResearchRun]:
    """List the current user's research runs, newest first."""
    try:
        return await service.list_runs(
            current_user.id,
            project_id=project_id,
            status=status_filter,
            skip=skip,
            limit=limit,
        )
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc


@router.get("/runs/{run_id}", response_model=ResearchRunDetail)
async def get_research_run(
    run: ResearchRun = Depends(get_owned_run),
    service: ResearchService = Depends(get_research_service),
) -> ResearchRunDetail:
    """Fetch one run with its full execution trace and agent transcript."""
    steps, messages = await service.get_trace(run)
    rerun_run_id = await service.find_rerun_id(run)
    added_paper_count = (
        await service.get_added_paper_count(run.parent_run_id)
        if run.parent_run_id is not None
        else None
    )
    return ResearchRunDetail.from_model(
        run,
        steps=[ResearchStepRead.model_validate(step) for step in steps],
        # `AgentMessage` needs the explicit bridge from its
        # `message_metadata` attribute to the `metadata` API field.
        messages=[AgentMessageRead.from_model(message) for message in messages],
        rerun_run_id=rerun_run_id,
        added_paper_count=added_paper_count,
    )


@router.post(
    "/runs/{run_id}/unsourced", response_model=ResearchRunRead, status_code=status.HTTP_201_CREATED
)
async def create_unsourced_run_route(
    run: ResearchRun = Depends(get_owned_run),
    service: ResearchService = Depends(get_research_service),
) -> ResearchRun:
    """Answer `run`'s query from general knowledge, on the caller's explicit request.

    Sprint 16 Phase 8.13: reached only after `run` already returned
    `insufficient_evidence` and the user pressed the dedicated control
    to leave the evidence boundary. Creates a new, separately auditable
    run rather than mutating `run` itself; `get_owned_run` already
    enforces ownership, transitively through the project, the same as
    every other run-scoped route.
    """
    try:
        return await service.create_unsourced_run(run)
    except UnsourcedSynthesisFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The unsourced answer could not be generated.",
        ) from exc


@router.post(
    "/runs/{run_id}/papers/import",
    response_model=PaperImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_suggested_papers_route(
    data: PaperImportRequest,
    run: ResearchRun = Depends(get_owned_run),
    service: ResearchService = Depends(get_research_service),
) -> PaperImportAccepted:
    """Import selected OpenAlex-suggested papers as project assets and,
    once every one reaches a final state, start exactly one re-run
    (Milestone 10 step 3: Add & re-run)."""
    try:
        papers = await service.import_papers(run, data.openalex_ids)
    except UnknownSuggestedPaperError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{exc}' is not a suggested paper for this run.",
        ) from exc
    except PaperNotOpenAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{exc}' has no open-access PDF to import.",
        ) from exc
    return PaperImportAccepted(run_id=run.id, papers=papers)


@router.post("/documents/{asset_id}/analyze", response_model=ResearchDocumentUnderstanding)
async def analyze_research_document_route(
    asset_id: uuid.UUID,
    data: AnalyzeDocumentRequest,
    project_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    service: ResearchService = Depends(get_research_service),
) -> ResearchDocumentUnderstanding:
    """Analyze a research document against a stated goal."""
    try:
        return await service.analyze_document(current_user.id, asset_id, project_id, data)
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/documents/{asset_id}/cross-paper-analysis", response_model=CrossPaperComparison)
async def analyze_cross_paper_route(
    asset_id: uuid.UUID,
    data: CrossPaperAnalysisRequest,
    project_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    service: ResearchService = Depends(get_research_service),
) -> CrossPaperComparison:
    """Analyze multiple research documents against a primary document."""
    try:
        return await service.synthesize_cross_paper(current_user.id, asset_id, project_id, data)
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Experiment Plan Engine (Sprint 16 Phase 6)
#
# Every route below produces or edits a PLAN, never an executed
# experiment -- there is no route here that runs code. Ownership is
# enforced exactly like every other research route: through the
# project the plan belongs to.
# ---------------------------------------------------------------------------

_EXPERIMENT_PLAN_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Experiment plan not found."
)


def _raise_validation(exc: ExperimentPlanValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/experiments/from-equation",
    response_model=ExperimentPlan,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment_from_equation_route(
    data: ExperimentPlanCreateFromEquation,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExperimentPlan:
    """Turn an equation into a reviewable experiment plan (Part E)."""
    try:
        return await service.create_from_equation(current_user.id, data)
    except ExperimentPlanAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.post(
    "/experiments/from-understanding",
    response_model=ExperimentPlan,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment_from_understanding_route(
    data: ExperimentPlanCreateFromUnderstanding,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExperimentPlan:
    """Turn a paper's structured research understanding into a reviewable
    experiment plan, respecting the stated research goal (Part M)."""
    try:
        return await service.create_from_understanding(current_user.id, data)
    except ExperimentPlanAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.get("/experiments/{plan_id}", response_model=ExperimentPlan)
async def get_experiment_plan_route(
    plan_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExperimentPlan:
    try:
        return await service.get_plan(current_user.id, plan_id)
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc


@router.get("/experiments", response_model=list[ExperimentPlan])
async def list_experiment_plans_route(
    project_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> list[ExperimentPlan]:
    try:
        return await service.list_plans(current_user.id, project_id)
    except ExperimentPlanAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc


@router.patch("/experiments/{plan_id}", response_model=ExperimentPlan)
async def update_experiment_plan_route(
    plan_id: uuid.UUID,
    data: ExperimentPlanUpdateRequest,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExperimentPlan:
    """Update a plan, recording every change in its version history
    (Part O). Changing a FIXED variable requires
    `allow_fixed_variable_change=true` (Part Y.4)."""
    try:
        return await service.update_plan(current_user.id, plan_id, data)
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.post("/experiments/{plan_id}/variants", response_model=ExperimentPlan)
async def create_experiment_variant_route(
    plan_id: uuid.UUID,
    data: ExperimentVariantCreateRequest,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExperimentPlan:
    """Add a named variant (Part I) -- planned only, never executed."""
    try:
        return await service.create_variant(current_user.id, plan_id, data)
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.post("/experiments/{plan_id}/sweep", response_model=ExperimentPlan)
async def create_experiment_sweep_route(
    plan_id: uuid.UUID,
    data: ExperimentSweepRequest,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExperimentPlan:
    """Expand a parameter sweep into planned variants for review (Part J)
    -- never executed here."""
    try:
        return await service.plan_sweep(current_user.id, plan_id, data)
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.post("/experiments/{plan_id}/test-cases/import", response_model=TestCaseImportResponse)
async def import_experiment_test_cases_route(
    plan_id: uuid.UUID,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> TestCaseImportResponse:
    """Import test cases from JSON/CSV/XLSX (Part G). Appends to the
    plan's existing test cases -- never overwrites them (Part Y.5)."""
    content = await file.read()
    try:
        return await service.import_test_cases(current_user.id, plan_id, content, file.content_type or "")
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.post(
    "/experiments/{plan_id}/execute",
    response_model=ExecutionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_experiment_variant_route(
    plan_id: uuid.UUID,
    data: ExperimentExecuteRequest,
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> ExecutionJob:
    """Request execution of ONE variant of an experiment plan (Sprint 16
    Phase 7B.23). Creates an `ExecutionJob(PENDING)`, commits it, and
    enqueues the launch task -- the existing guard pipeline runs to the
    Docker boundary and stops there; no container is launched, because
    no launcher exists yet. Connective tissue, not a user-visible
    feature: nothing observable happens beyond `VALIDATING` plus a
    `PENDING_CREATE` attempt row."""
    try:
        return await service.request_execution(current_user.id, plan_id, data.variant_id)
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc
    except ExperimentPlanValidationError as exc:
        raise _raise_validation(exc) from exc


@router.get("/experiments/{plan_id}/visualization", response_model=VisualizationData)
async def get_experiment_visualization_route(
    plan_id: uuid.UUID,
    input_name: str = Query(...),
    output_name: str = Query(...),
    current_user: User = Depends(get_current_user),
    service: ExperimentPlanService = Depends(get_experiment_service),
) -> VisualizationData:
    """Raw trace data for the frontend to render with Plotly.js (Part
    H/R) -- never a rendered image, never a causal claim."""
    try:
        return await service.get_visualization_data(current_user.id, plan_id, input_name, output_name)
    except ExperimentPlanNotFoundError as exc:
        raise _EXPERIMENT_PLAN_NOT_FOUND from exc

