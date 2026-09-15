"""HTTP routes for asset upload, retrieval, update, deletion, and search.

Literal-path routes (`/upload`, `/search`, `/download/{id}`) are
declared before the generic `/{asset_id}` route. FastAPI's UUID path
converter already wouldn't match a literal segment like "search", but
this ordering keeps it correct by construction rather than by that
implementation detail.
"""

import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from app.modules.assets.enums import AssetStatus, AssetType
from app.modules.assets.schemas import AssetRead, AssetUpdate
from app.modules.assets.service import (
    AssetNotFoundError,
    AssetService,
    DuplicateAssetError,
    GeneratedAssetReprocessError,
    ProjectAccessDeniedError,
    get_asset_service,
)
from app.modules.assets.validators import AssetValidationError
from app.modules.auth.models import User
from app.modules.auth.security import get_current_user

router = APIRouter(prefix="/assets", tags=["Assets"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
_UNSAFE_HEADER_CHARS = re.compile(r'[\r\n"]')


def _parse_tags(tags: str | None) -> list[str]:
    """Parse a comma-separated tag list from a form or query string."""
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def _content_disposition(filename: str) -> str:
    """Build a `Content-Disposition` header value safe against header
    injection from a user-supplied filename (strips CR/LF and quotes)."""
    sanitized = _UNSAFE_HEADER_CHARS.sub("_", filename)
    return f'attachment; filename="{sanitized}"'


@router.post("/upload", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    project_id: uuid.UUID = Form(...),
    title: str | None = Form(default=None),
    description: str | None = Form(default=None),
    asset_type: AssetType | None = Form(default=None),
    tags: str | None = Form(default=None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> AssetRead:
    """Upload a file as a new asset within one of the current user's projects."""
    try:
        asset = await service.upload(
            current_user_id=current_user.id,
            project_id=project_id,
            file=file,
            title=title,
            description=description,
            asset_type=asset_type,
            tags=_parse_tags(tags),
        )
    except ProjectAccessDeniedError as exc:
        raise _NOT_FOUND from exc
    except AssetValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except DuplicateAssetError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AssetRead.from_model(asset)


@router.get("/search", response_model=list[AssetRead])
async def search_assets(
    q: str | None = Query(default=None, min_length=1, max_length=255),
    project_id: uuid.UUID | None = Query(default=None),
    asset_type: AssetType | None = Query(default=None),
    status_filter: AssetStatus | None = Query(default=None, alias="status"),
    tags: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetRead]:
    """Search the current user's assets by text, project, type, status, or tags."""
    try:
        assets = await service.search(
            current_user.id,
            query=q,
            project_id=project_id,
            asset_type=asset_type,
            status=status_filter,
            tags=_parse_tags(tags),
            skip=skip,
            limit=limit,
        )
    except ProjectAccessDeniedError as exc:
        raise _NOT_FOUND from exc
    return [AssetRead.from_model(asset) for asset in assets]


@router.post("/{asset_id}/process", response_model=AssetRead, status_code=status.HTTP_202_ACCEPTED)
async def reprocess_asset(
    asset_id: uuid.UUID,
    force: bool = Query(
        default=False,
        description=(
            "Re-run the pipeline even if this asset already completed "
            "extraction, understanding, and embedding successfully. "
            "Without this, reprocessing an already-fully-processed asset "
            "is a no-op (Sprint 12.8)."
        ),
    ),
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> AssetRead:
    """Manually (re)queue an owned asset for the extract/chunk pipeline.

    Assets are queued automatically on upload; this endpoint is for
    retrying a `FAILED` asset or reprocessing after the pipeline
    changes. A `FAILED` or otherwise incomplete asset is always
    reprocessed regardless of `force`; only an asset that already
    completed every stage successfully is skipped, and only when
    `force` is not set.
    """
    try:
        asset = await service.reprocess(current_user.id, asset_id, force=force)
    except AssetNotFoundError as exc:
        raise _NOT_FOUND from exc
    except GeneratedAssetReprocessError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generated assets (reports) cannot be reprocessed.",
        ) from exc
    return AssetRead.from_model(asset)


@router.get("/download/{asset_id}")
async def download_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> Response:
    """Download the raw file content of an owned asset."""
    try:
        asset, content = await service.get_file_for_download(current_user.id, asset_id)
    except AssetNotFoundError as exc:
        raise _NOT_FOUND from exc
    return Response(
        content=content,
        media_type=asset.mime_type,
        headers={"Content-Disposition": _content_disposition(asset.file_name)},
    )


@router.get("", response_model=list[AssetRead])
async def list_assets(
    project_id: uuid.UUID | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetRead]:
    """List the current user's assets, optionally scoped to one project."""
    try:
        assets = await service.list_assets(
            current_user.id, project_id=project_id, skip=skip, limit=limit
        )
    except ProjectAccessDeniedError as exc:
        raise _NOT_FOUND from exc
    return [AssetRead.from_model(asset) for asset in assets]


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> AssetRead:
    """Fetch a single asset owned by the current user."""
    try:
        asset = await service.get_owned(current_user.id, asset_id)
    except AssetNotFoundError as exc:
        raise _NOT_FOUND from exc
    return AssetRead.from_model(asset)


@router.patch("/{asset_id}", response_model=AssetRead)
async def update_asset(
    asset_id: uuid.UUID,
    data: AssetUpdate,
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> AssetRead:
    """Partially update an asset's metadata (not its underlying file)."""
    try:
        asset = await service.update(current_user.id, asset_id, data)
    except AssetNotFoundError as exc:
        raise _NOT_FOUND from exc
    return AssetRead.from_model(asset)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AssetService = Depends(get_asset_service),
) -> None:
    """Delete an asset owned by the current user, including its stored file."""
    try:
        await service.delete(current_user.id, asset_id)
    except AssetNotFoundError as exc:
        raise _NOT_FOUND from exc
