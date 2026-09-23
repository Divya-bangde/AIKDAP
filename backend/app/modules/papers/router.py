"""HTTP routes for the project paper map (Phase 4).

Project-scoped like every other project resource: a project that is
missing or not the caller's is a 404 on both routes.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.papers.openalex import OpenAlexLookupError
from app.modules.papers.schemas import (
    PaperGraph,
    ProjectPaperImportAccepted,
    ProjectPaperImportRequest,
)
from app.modules.papers.service import (
    OutsidePaperNotFoundError,
    OutsidePaperNotOpenAccessError,
    PaperGraphService,
    ProjectNotFoundError,
)

router = APIRouter(prefix="/projects", tags=["Papers"])

_PROJECT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Project not found."
)


async def get_paper_graph_service(session: AsyncSession = Depends(get_db)) -> PaperGraphService:
    return PaperGraphService(session)


@router.get("/{project_id}/paper-graph", response_model=PaperGraph)
async def get_paper_graph(
    project_id: uuid.UUID,
    include_external: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    service: PaperGraphService = Depends(get_paper_graph_service),
) -> PaperGraph:
    """The citation graph of the project's papers.

    A link means project paper A references project paper B, both
    matched in OpenAlex. With `include_external`, up to 15 outside works
    cited by at least 2 project papers are added as `external` nodes.
    """
    try:
        return await service.get_graph(
            current_user.id, project_id, include_external=include_external
        )
    except ProjectNotFoundError as exc:
        raise _PROJECT_NOT_FOUND from exc


@router.post(
    "/{project_id}/papers/import",
    response_model=ProjectPaperImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_outside_paper(
    project_id: uuid.UUID,
    data: ProjectPaperImportRequest,
    current_user: User = Depends(get_current_user),
    service: PaperGraphService = Depends(get_paper_graph_service),
) -> ProjectPaperImportAccepted:
    """Queue an outside work's open-access PDF for import into the project."""
    try:
        await service.import_outside_paper(current_user.id, project_id, data.openalex_id)
    except ProjectNotFoundError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except OutsidePaperNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="OpenAlex has no such work."
        ) from exc
    except OutsidePaperNotOpenAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This paper has no open-access PDF to import.",
        ) from exc
    except OpenAlexLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="OpenAlex could not be reached."
        ) from exc
    return ProjectPaperImportAccepted(openalex_id=data.openalex_id, status="queued")
