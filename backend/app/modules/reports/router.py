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

from app.modules.assets.router import _content_disposition
from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.reports.schemas import ReportGenerateRequest, ReportGenerationAccepted, ReportRead
from app.modules.reports.service import (
    NoProcessedDocumentsError,
    ProjectAccessDeniedError,
    ReportNotFoundError,
    ReportNotReadyError,
    ReportNotRetryableError,
    ReportService,
    get_report_service,
)

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
