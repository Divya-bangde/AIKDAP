"""HTTP routes for project CRUD, scoped to the authenticated user."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, cursor_scope, read_page
from app.database.session import get_db
from app.modules.projects.models import Project
from app.modules.assets.models import Asset
from app.modules.assets.enums import AssetType
from app.modules.research.models import ResearchRun
from app.modules.research.enums import ResearchGroundingStatus, ResearchRunStatus

from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate, WorkspaceSummary
from app.modules.projects.service import (
    ProjectNotFoundError,
    ProjectService,
    get_project_service,
)

router = APIRouter(prefix="/projects", tags=["Projects"])

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Project not found."
)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectRead:
    """Create a new project owned by the current user."""
    project = await service.create(current_user.id, data)
    return ProjectRead.from_project(project, current_user.persona)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectRead]:
    """List the current user's projects."""
    projects = await service.list_for_owner(current_user.id, skip=skip, limit=limit)
    return [ProjectRead.from_project(project, current_user.persona) for project in projects]


@router.get("/page", response_model=Page[ProjectRead])
async def project_page(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Page[ProjectRead]:
    rows, next_cursor = await read_page(
        session, select(Project).where(Project.owner_id == current_user.id), Project,
        cursor=cursor, scope=cursor_scope("projects", current_user.id), limit=limit,
    )
    return Page(items=[ProjectRead.from_project(row, current_user.persona) for row in rows],
                next_cursor=next_cursor)


@router.get("/summary", response_model=WorkspaceSummary)
async def workspace_summary(
    project_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    service: ProjectService = Depends(get_project_service),
) -> dict[str, int]:
    """All owned records, including archived items; documents have type=document.

    Runs include every status. Grounded answers are completed research runs
    explicitly marked grounded with a nonempty final answer. No page limit.
    """
    if project_id is not None:
        try:
            await service.get_owned(current_user.id, project_id)
        except ProjectNotFoundError as exc:
            raise _NOT_FOUND from exc
    projects = select(Project.id).where(Project.owner_id == current_user.id)
    if project_id is not None:
        projects = projects.where(Project.id == project_id)
    asset_filter = (Asset.owner_id == current_user.id, Asset.project_id.in_(projects))
    run_filter = (ResearchRun.owner_id == current_user.id, ResearchRun.project_id.in_(projects))
    # Scalar subqueries share a single database snapshot, without parallel sessions.
    row = (await session.execute(select(
        select(func.count()).select_from(projects.subquery()).scalar_subquery(),
        select(func.count()).select_from(Asset).where(*asset_filter).scalar_subquery(),
        select(func.count()).select_from(Asset).where(*asset_filter, Asset.asset_type == AssetType.DOCUMENT).scalar_subquery(),
        select(func.count()).select_from(ResearchRun).where(*run_filter).scalar_subquery(),
        select(func.count()).select_from(ResearchRun).where(
            *run_filter, ResearchRun.status == ResearchRunStatus.COMPLETED,
            ResearchRun.grounding_status == ResearchGroundingStatus.GROUNDED,
            func.length(func.trim(ResearchRun.final_answer)) > 0,
        ).scalar_subquery(),
    ))).one()
    return dict(zip(("projects", "assets", "documents", "runs", "grounded_answers"), row, strict=True))


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectRead:
    """Fetch a single project owned by the current user."""
    try:
        project = await service.get_owned(current_user.id, project_id)
    except ProjectNotFoundError as exc:
        raise _NOT_FOUND from exc
    return ProjectRead.from_project(project, current_user.persona)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectRead:
    """Partially update a project owned by the current user."""
    try:
        project = await service.update(current_user.id, project_id, data)
    except ProjectNotFoundError as exc:
        raise _NOT_FOUND from exc
    return ProjectRead.from_project(project, current_user.persona)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> None:
    """Delete a project owned by the current user."""
    try:
        await service.delete(current_user.id, project_id)
    except ProjectNotFoundError as exc:
        raise _NOT_FOUND from exc
