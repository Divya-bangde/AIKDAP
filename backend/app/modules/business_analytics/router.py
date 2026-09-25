"""HTTP routes for business analytics (Milestone 6).

Step traces and live streams reuse `/reports/{asset_id}/steps*`, which
already serve any owned GENERATED asset."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.analytics.dataset import DatasetReadError
from app.integrations.kaggle.client import KaggleError, KaggleNotConfiguredError
from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.business_analytics.schemas import (
    AnalysisCreate,
    AnalysisRead,
    DatasetProfileRead,
    KaggleFileRead,
    KaggleImportAccepted,
    KaggleImportRequest,
)
from app.modules.business_analytics.service import (
    AnalyticsNotFoundError,
    AnalyticsService,
    DatasetNotReadyError,
    ProjectAccessDeniedError,
    get_analytics_service,
)

router = APIRouter(prefix="/analytics", tags=["Business Analytics"])
project_router = APIRouter(tags=["Business Analytics"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
_NOT_READY = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="This asset is not a processed CSV/XLSX dataset yet.",
)


@router.get("/datasets/{dataset_id}/profile", response_model=DatasetProfileRead)
async def get_profile(
    dataset_id: uuid.UUID,
    sheet: str | None = None,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> DatasetProfileRead:
    try:
        return DatasetProfileRead.model_validate(await service.get_profile(current_user.id, dataset_id, sheet))
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc
    except DatasetNotReadyError as exc:
        raise _NOT_READY from exc
    except DatasetReadError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/analyses", response_model=AnalysisRead, status_code=status.HTTP_202_ACCEPTED)
async def create_analysis(
    dataset_id: uuid.UUID,
    data: AnalysisCreate,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalysisRead:
    try:
        asset = await service.create_analysis(current_user.id, dataset_id, data.question.strip(), data.sheet)
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc
    except DatasetNotReadyError as exc:
        raise _NOT_READY from exc
    return AnalysisRead.from_asset(asset)


@router.get("/datasets/{dataset_id}/analyses", response_model=list[AnalysisRead])
async def list_analyses(
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> list[AnalysisRead]:
    try:
        return [AnalysisRead.from_asset(asset) for asset in await service.list_analyses(current_user.id, dataset_id)]
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc


@router.get("/analyses/{asset_id}", response_model=AnalysisRead)
async def get_analysis(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalysisRead:
    try:
        return AnalysisRead.from_asset(await service.get_analysis(current_user.id, asset_id))
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc


def _kaggle_http_error(exc: KaggleError) -> HTTPException:
    if isinstance(exc, KaggleNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Kaggle is not configured.")
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/kaggle/{owner}/{dataset}/files", response_model=list[KaggleFileRead])
async def list_kaggle_files(
    owner: str, dataset: str,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> list[KaggleFileRead]:
    try:
        return [KaggleFileRead(name=item.name, size=item.size) for item in await service.list_kaggle_files(owner, dataset)]
    except KaggleError as exc:
        raise _kaggle_http_error(exc) from exc


@project_router.post("/projects/{project_id}/analytics/kaggle/import", response_model=KaggleImportAccepted, status_code=status.HTTP_202_ACCEPTED)
async def import_kaggle(
    project_id: uuid.UUID, data: KaggleImportRequest,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> KaggleImportAccepted:
    try:
        await service.import_from_kaggle(current_user.id, project_id, data)
    except ProjectAccessDeniedError as exc:
        raise _NOT_FOUND from exc
    except KaggleError as exc:
        raise _kaggle_http_error(exc) from exc
    return KaggleImportAccepted()
