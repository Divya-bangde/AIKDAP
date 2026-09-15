"""Business logic for generating and downloading reports.

Ownership is enforced transitively, exactly like `AssetService`:
"exists but not yours" and "doesn't exist" both surface as
`ReportNotFoundError`/`ProjectAccessDeniedError` so ownership is never
leaked to the caller.
"""

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.projects.repository import ProjectRepository
from app.modules.reports.export import render_docx, render_pdf
from app.modules.reports.repository import ReportRepository
from app.modules.reports.schemas import ReportKind
from app.workers.tasks import generate_report

_KIND_ASSET_TYPE: dict[ReportKind, AssetType] = {
    ReportKind.STUDY_SUMMARY: AssetType.SUMMARY,
    ReportKind.PROJECT_SYNOPSIS: AssetType.REPORT,
}
_KIND_TITLE: dict[ReportKind, str] = {
    ReportKind.STUDY_SUMMARY: "Study Summary",
    ReportKind.PROJECT_SYNOPSIS: "Project Synopsis",
}
_FORMAT_RENDERERS = {
    "docx": (render_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": (render_pdf, "application/pdf"),
}


class ProjectAccessDeniedError(Exception):
    """Raised when the caller does not own the project."""


class ReportNotFoundError(Exception):
    """Raised when a report asset does not exist or is not owned by the caller."""


class NoProcessedDocumentsError(Exception):
    """Raised when a project has no processed documents to report on."""


class ReportNotReadyError(Exception):
    """Raised when a download is requested before generation finished."""


def _slug(text: str) -> str:
    return "-".join(text.lower().split()) or "report"


class ReportService:
    """Coordinates report generation and download rendering."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._reports = ReportRepository(session)
        self._assets = AssetRepository(session)
        self._projects = ProjectRepository(session)

    async def _ensure_project_owned(self, owner_id: uuid.UUID, project_id: uuid.UUID):
        project = await self._projects.get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectAccessDeniedError(project_id)
        return project

    async def generate_synopsis(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, kind: ReportKind
    ) -> Asset:
        """Create a `pending` report asset and dispatch its generation.

        The no-processed-documents check happens here, before any asset
        or Celery job is created -- a `422` never leaves a half-created
        asset behind (spec section 7).
        """
        await self._ensure_project_owned(owner_id, project_id)

        documents = await self._reports.list_processed_documents(project_id)
        if not documents:
            raise NoProcessedDocumentsError(project_id)

        title = _KIND_TITLE[kind]
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title=title,
            description=None,
            asset_type=_KIND_ASSET_TYPE[kind],
            status=AssetStatus.ACTIVE,
            # No file is ever stored for a generated report -- it is
            # rendered on demand from `asset_metadata["sections"]` (spec
            # section 4: "nothing is stored twice"). These fields exist
            # only because the column is `NOT NULL`; nothing reads them
            # for a GENERATED report asset.
            mime_type="application/json",
            file_name=f"{_slug(title)}.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            asset_metadata={"kind": kind.value, "sections": []},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.PENDING,
        )
        created = await self._assets.create(asset)
        await self._session.commit()

        # Enqueue only after the commit succeeds, matching
        # `AssetService.upload`: a worker picking this up must be able
        # to find the row it's processing.
        generate_report.delay(str(created.id))
        return created

    async def get_owned_report(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        """Fetch a report asset, ensuring it belongs to the given user."""
        asset = await self._assets.get_by_id(asset_id)
        if asset is None or asset.owner_id != owner_id or asset.source is not AssetSource.GENERATED:
            raise ReportNotFoundError(asset_id)
        return asset

    async def render_download(
        self, owner_id: uuid.UUID, asset_id: uuid.UUID, format: str
    ) -> tuple[bytes, str, str]:
        """Render `asset`'s stored sections to `format`, on demand.

        Never renders a partial document: a report that has not reached
        `COMPLETED` raises `ReportNotReadyError` rather than rendering
        whatever sections happen to be present so far (spec section 7:
        "no partial document may ever be presented as complete").
        """
        asset = await self.get_owned_report(owner_id, asset_id)
        if asset.processing_status is not AssetProcessingStatus.COMPLETED:
            raise ReportNotReadyError(asset_id)

        renderer, media_type = _FORMAT_RENDERERS[format]
        sections = asset.asset_metadata.get("sections", [])
        content = renderer(asset.title, sections)
        filename = f"{_slug(asset.title)}.{format}"
        return content, media_type, filename


async def get_report_service(session: AsyncSession = Depends(get_db)) -> ReportService:
    """FastAPI dependency provider for `ReportService`."""
    return ReportService(session)
