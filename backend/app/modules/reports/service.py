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
from app.modules.reports.schemas import BUILD_PLAN_KIND, ReportKind
from app.modules.research.models import ResearchStep
from app.modules.research.repository import ResearchStepRepository
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


class AssetSelectionError(Exception):
    """Raised when a build-plan selection contains no usable document.

    Distinct from `NoProcessedDocumentsError`: the project does have
    processed documents, but the papers the caller picked are not among
    them -- a different message, and a different thing for the user to
    fix.
    """


class AssetNotInProjectError(Exception):
    """Raised when a selected asset id is not in the given project.

    Surfaces as `404`, not `403`: consistent with the rest of the
    module, "not yours" and "doesn't exist" are indistinguishable to
    the caller (spec section 7).
    """


class ReportNotReadyError(Exception):
    """Raised when a download is requested before generation finished."""


class ReportNotRetryableError(Exception):
    """Raised when a retry is requested for a report that has not failed."""


def _slug(text: str) -> str:
    return "-".join(text.lower().split()) or "report"


class ReportService:
    """Coordinates report generation and download rendering."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._reports = ReportRepository(session)
        self._assets = AssetRepository(session)
        self._projects = ProjectRepository(session)
        self._steps = ResearchStepRepository(session)

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

    async def generate_build_plan(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> Asset:
        """Create a `pending` build-plan asset and dispatch its generation.

        Validation order matters and is deliberate:

        1. project ownership -> `404`, so nothing about a project the
           caller cannot see is ever revealed;
        2. any selected id not in that project -> `404`, same reason;
        3. the project having no processed documents at all -> `422`;
        4. the selection naming no processed document -> `422`.

        Steps 3 and 4 are separate because they are different user
        problems: "upload something" versus "wait for processing". As
        with `generate_synopsis`, every check runs before any asset or
        Celery job exists, so a rejection leaves nothing behind.
        """
        await self._ensure_project_owned(owner_id, project_id)

        in_project = await self._reports.list_project_asset_ids(project_id)
        unknown = [asset_id for asset_id in asset_ids if asset_id not in in_project]
        if unknown:
            raise AssetNotInProjectError(unknown)

        processed = await self._reports.list_processed_documents(project_id)
        if not processed:
            raise NoProcessedDocumentsError(project_id)

        selected = await self._reports.list_processed_documents_by_ids(project_id, asset_ids)
        if not selected:
            raise AssetSelectionError(asset_ids)

        title = "Build Plan"
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title=title,
            description=None,
            asset_type=AssetType.REPORT,
            status=AssetStatus.ACTIVE,
            # As with a synopsis, no file is stored: the document is
            # rendered on demand from `asset_metadata["sections"]`.
            mime_type="application/json",
            file_name=f"{_slug(title)}.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            # `asset_ids` is persisted, not just passed to the worker: a
            # retry re-runs from the asset row alone, and the trace
            # should record which papers this plan was built from.
            asset_metadata={
                "kind": BUILD_PLAN_KIND,
                "asset_ids": [str(asset_id) for asset_id in asset_ids],
                "sections": [],
            },
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.PENDING,
        )
        created = await self._assets.create(asset)
        await self._session.commit()

        generate_report.delay(str(created.id))
        return created

    async def get_owned_report(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        """Fetch a report asset, ensuring it belongs to the given user."""
        asset = await self._assets.get_by_id(asset_id)
        if asset is None or asset.owner_id != owner_id or asset.source is not AssetSource.GENERATED:
            raise ReportNotFoundError(asset_id)
        return asset

    async def get_report(
        self, owner_id: uuid.UUID, asset_id: uuid.UUID
    ) -> tuple[Asset, list[ResearchStep]]:
        """Fetch an owned report asset and its step trace, in order."""
        asset = await self.get_owned_report(owner_id, asset_id)
        steps = await self._steps.list_by_asset(asset.id)
        return asset, steps

    async def step_snapshot(self, asset_id: uuid.UUID) -> tuple[list[ResearchStep], bool]:
        """A report's steps plus whether generation has finished, for
        the step stream. Ownership is checked by the caller when the
        stream opens."""
        asset = await self._session.get(Asset, asset_id)
        steps = await self._steps.list_by_asset(asset_id)
        finished = asset is None or asset.processing_status in (
            AssetProcessingStatus.COMPLETED,
            AssetProcessingStatus.FAILED,
        )
        return steps, finished

    async def retry_report(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        """Reset a failed report to `pending` and re-enqueue it.

        Only a `failed` report is retryable -- anything else (pending,
        running, completed) raises `ReportNotRetryableError`. Earlier
        attempts' steps are kept; the new attempt appends to the trace.
        Two concurrent retries may both enqueue, but the worker's
        atomic `pending -> running` claim lets exactly one run.
        """
        asset = await self.get_owned_report(owner_id, asset_id)
        if asset.processing_status is not AssetProcessingStatus.FAILED:
            raise ReportNotRetryableError(asset_id)

        asset.processing_status = AssetProcessingStatus.PENDING
        asset.processing_error = None
        asset.asset_metadata = {**asset.asset_metadata, "sections": []}
        await self._session.commit()

        generate_report.delay(str(asset.id))
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
