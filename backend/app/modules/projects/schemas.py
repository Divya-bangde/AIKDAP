"""Pydantic v2 request/response schemas for the projects module."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.auth.models import Persona
from app.modules.projects.models import Project, ProjectStatus, ProjectType


class ProjectCreate(BaseModel):
    """Payload for creating a new project."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    project_type: ProjectType = ProjectType.RESEARCH
    color: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=50)


class ProjectUpdate(BaseModel):
    """Payload for partially updating a project. Unset fields are left untouched."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    project_type: ProjectType | None = None
    status: ProjectStatus | None = None
    color: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=50)
    #: Explicit null clears the override so the project follows the user's persona.
    persona_override: Persona | None = None


class ProjectRead(BaseModel):
    """Public representation of a project."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    project_type: ProjectType
    status: ProjectStatus
    color: str | None
    icon: str | None
    created_at: datetime
    updated_at: datetime
    persona_override: Persona | None
    effective_persona: Persona

    @classmethod
    def from_project(cls, project: Project, owner_persona: Persona) -> "ProjectRead":
        """Build the response, resolving the persona the project behaves as."""
        fields = {
            name: getattr(project, name)
            for name in cls.model_fields
            if name != "effective_persona"
        }
        return cls(**fields, effective_persona=project.effective_persona(owner_persona))
