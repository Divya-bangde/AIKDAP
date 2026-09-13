"""HTTP routes for project CRUD, scoped to the authenticated user."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate
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
