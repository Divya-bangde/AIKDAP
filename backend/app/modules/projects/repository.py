"""Data-access layer for the `Project` model.

Contains only persistence operations; transaction boundaries (commit)
and ownership/business rules live in `service.py`.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.projects.models import Project, ProjectType


class ProjectRepository:
    """Encapsulates all direct database access for `Project` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        """Fetch a project by primary key, or None if not found."""
        return await self._session.get(Project, project_id)

    async def list_by_owner(
        self, owner_id: uuid.UUID, *, skip: int = 0, limit: int = 100
    ) -> list[Project]:
        """List projects owned by a user, newest first."""
        result = await self._session.execute(
            select(Project)
            .where(Project.owner_id == owner_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        name: str,
        description: str | None,
        project_type: ProjectType,
        color: str | None,
        icon: str | None,
    ) -> Project:
        """Insert a new project row and flush to populate generated fields."""
        project = Project(
            owner_id=owner_id,
            name=name,
            description=description,
            project_type=project_type,
            color=color,
            icon=icon,
        )
        self._session.add(project)
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def delete(self, project: Project) -> None:
        """Delete a project row."""
        await self._session.delete(project)
        await self._session.flush()
