"""HTTP routes for report generation and download.

Two different resource prefixes on purpose (spec section 5):
`/projects/{project_id}/reports/synopsis` to generate, `/reports/{asset_id}/
download` to fetch -- so `router` carries no single prefix and declares
each full path itself, the same shape `research.router` would need if research
routes ever split across two resource trees.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.modules.assets.router import _content_disposition
from app.modules.auth.models import User
from app.database.session import open_session
from app.modules.auth.security import (
    STREAM_TOKEN_TTL_SECONDS,
    create_stream_token,
    decode_stream_token,
    get_current_user,
)
from app.modules.reports.schemas import (
    BuildPlanRequest,
    ReportGenerateRequest,
    ReportGenerationAccepted,
    ReportRead,
)
from app.modules.reports.service import (
    AssetNotInProjectError,
    AssetSelectionError,
    NoProcessedDocumentsError,
    ProjectAccessDeniedError,
    ReportNotFoundError,
    ReportNotReadyError,
    ReportNotRetryableError,
    ReportService,
    get_report_service,
)

from app.modules.research.schemas import ResearchStepRead, StepStreamToken
from app.modules.research.step_events import report_steps_channel
from app.modules.research.step_stream import SSE_HEADERS, step_event_stream

router = APIRouter(tags=["Reports"])

_PROJECT_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
_REPORT_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")


@router.post(
    "/projects/{project_id}/reports/synopsis",
    response_model=ReportGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_synopsis_route(
    project_id: uuid.UUID,
    data: ReportGenerateRequest,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportGenerationAccepted:
    """Start generating a Study Summary or Project Synopsis for a
    project's processed documents. Runs in the Celery worker; poll
    `GET /reports/{asset_id}` for `processing_status`."""
    try:
        asset = await service.generate_synopsis(current_user.id, project_id, data.kind)
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except NoProcessedDocumentsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This project has no processed documents to report on yet.",
        ) from exc
    return ReportGenerationAccepted(asset_id=asset.id, status=asset.processing_status)


@router.post(
    "/projects/{project_id}/reports/build-plan",
    response_model=ReportGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_build_plan_route(
    project_id: uuid.UUID,
    data: BuildPlanRequest,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportGenerationAccepted:
    """Start generating a build plan from the selected papers. Runs in
    the Celery worker; poll `GET /reports/{asset_id}` for
    `processing_status`, then download via
    `GET /reports/{asset_id}/download`."""
    try:
        asset = await service.generate_build_plan(current_user.id, project_id, data.asset_ids)
    except (ProjectAccessDeniedError, AssetNotInProjectError) as exc:
        # One 404 for both: a selection naming an asset outside the
        # project must not reveal whether that asset exists elsewhere.
        raise _PROJECT_NOT_FOUND from exc
    except NoProcessedDocumentsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This project has no processed documents to build a plan from yet.",
        ) from exc
    except AssetSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The papers you selected are still processing. Pick one that has finished.",
        ) from exc
    return ReportGenerationAccepted(asset_id=asset.id, status=asset.processing_status)


@router.get("/reports/{asset_id}")
async def get_report_route(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportRead:
    """A report's status, error, sections, and full step trace."""
    try:
        asset, steps = await service.get_report(current_user.id, asset_id)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    return ReportRead.from_report(asset, steps)


@router.get("/reports/{asset_id}/steps", response_model=list[ResearchStepRead])
async def list_report_steps(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> list[ResearchStepRead]:
    """The report's workflow steps, every attempt, in order."""
    try:
        _, steps = await service.get_report(current_user.id, asset_id)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    return [ResearchStepRead.model_validate(step) for step in steps]


@router.post("/reports/{asset_id}/steps/stream-token", response_model=StepStreamToken)
async def create_report_steps_stream_token(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> StepStreamToken:
    """Issue a ~60s token that opens this report's step stream, and only it."""
    try:
        await service.get_owned_report(current_user.id, asset_id)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    return StepStreamToken(
        token=create_stream_token(current_user.id, f"report:{asset_id}"),
        expires_in=STREAM_TOKEN_TTL_SECONDS,
    )


@router.get("/reports/{asset_id}/steps/stream")
async def stream_report_steps(
    asset_id: uuid.UUID, token: str = Query(..., min_length=1)
) -> StreamingResponse:
    """Server-Sent Events for a report run; same protocol as a research
    run's stream (`research.step_stream`)."""
    user_id = decode_stream_token(token, resource=f"report:{asset_id}")
    async with open_session() as session:
        try:
            await ReportService(session).get_owned_report(user_id, asset_id)
        except ReportNotFoundError as exc:
            raise _REPORT_NOT_FOUND from exc

    return StreamingResponse(
        step_event_stream(
            channel=report_steps_channel(asset_id),
            load=lambda session: ReportService(session).step_snapshot(asset_id),
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/reports/{asset_id}/download")
async def download_report_route(
    asset_id: uuid.UUID,
    format: Literal["docx", "pdf"] = Query(...),
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> Response:
    """Render a completed report to `format`, from its stored sections."""
    try:
        content, media_type, filename = await service.render_download(current_user.id, asset_id, format)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    except ReportNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This report is still generating."
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post(
    "/reports/{asset_id}/retry",
    response_model=ReportGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_report_route(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportGenerationAccepted:
    """Re-run a failed report. Owner-only (404 otherwise); only a
    `failed` report is retryable (409 otherwise)."""
    try:
        asset = await service.retry_report(current_user.id, asset_id)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    except ReportNotRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a failed report can be retried."
        ) from exc
    return ReportGenerationAccepted(asset_id=asset.id, status=asset.processing_status)
