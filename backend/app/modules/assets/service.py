"""Business logic for asset upload, retrieval, update, deletion, and search.

Ownership is enforced transitively: an asset belongs to a project, and
a user may only touch assets in projects they own. As with the
projects module, "exists but not yours" and "doesn't exist" both
surface as `AssetNotFoundError` so ownership is never leaked to the
caller. AI-profile generation (summaries, keywords, embeddings) is out
of scope here — every asset is created with a default, empty
`AIProfile` for future sprints to populate.
"""

import hashlib
import uuid
from pathlib import Path

from fastapi import Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging.logger import get_logger
from app.database.session import get_db
from app.modules.assets.ai_profile import AIProfile, AIProfileStatus
from app.modules.assets.enums import (
    AssetProcessingStatus,
    AssetSource,
    AssetStatus,
    AssetType,
    EmbeddingStatus,
)
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.schemas import AssetUpdate
from app.modules.assets.storage import StorageProvider, get_storage_provider, sanitize_filename
from app.modules.assets.validators import (
    validate_content_matches_mime,
    validate_extension,
    validate_extension_matches_mime,
    validate_file_size,
    validate_mime_type,
)
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.projects.repository import ProjectRepository
from app.workers.tasks import process_uploaded_asset

logger = get_logger(__name__)

_ORM_FIELD_ALIASES = {"metadata": "asset_metadata"}


class AssetNotFoundError(Exception):
    """Raised when an asset does not exist or is not owned by the caller."""


class ProjectAccessDeniedError(Exception):
    """Raised when the caller does not own the project an asset belongs to."""


class DuplicateAssetError(Exception):
    """Raised when a byte-identical file already exists (active) in the
    same project. Carries the existing asset so the caller can point
    the user at it instead of a bare rejection."""

    def __init__(self, existing_asset: Asset) -> None:
        self.existing_asset = existing_asset
        super().__init__(
            f"An identical file already exists in this project as "
            f"'{existing_asset.file_name}' (asset {existing_asset.id})."
        )


class AssetService:
    """Coordinates asset upload, retrieval, update, deletion, and search."""

    def __init__(self, session: AsyncSession, storage: StorageProvider) -> None:
        self._session = session
        self._storage = storage
        self._repository = AssetRepository(session)
        self._projects = ProjectRepository(session)

    async def _ensure_project_owned(self, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self._projects.get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectAccessDeniedError(project_id)

    async def upload(
        self,
        *,
        current_user_id: uuid.UUID,
        project_id: uuid.UUID,
        file: UploadFile,
        title: str | None,
        description: str | None,
        asset_type: AssetType | None,
        tags: list[str],
    ) -> Asset:
        """Validate, persist to storage, and record a new uploaded asset."""
        await self._ensure_project_owned(current_user_id, project_id)

        # Sanitize once, up front: the client-supplied filename is
        # untrusted and may contain path-traversal sequences. Every
        # downstream use (extension check, title, DB record, storage
        # path) must derive from this same safe value, or the stored
        # `file_name` would diverge from what's actually on disk.
        file_name = sanitize_filename(file.filename or "upload")
        extension = validate_extension(file_name)
        mime_type = validate_mime_type(file.content_type)
        validate_extension_matches_mime(extension, mime_type)

        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        declared_size = file.size
        if declared_size is not None:
            validate_file_size(declared_size, max_bytes=max_bytes)

        content = await file.read()
        validate_file_size(len(content), max_bytes=max_bytes)
        validate_content_matches_mime(content, mime_type)

        checksum = hashlib.sha256(content).hexdigest()

        # Sprint 12.4: a byte-identical file already active in this
        # project would otherwise re-run extraction, Qwen understanding,
        # and embedding from scratch for content already fully indexed
        # -- `checksum` was already computed and stored (indexed) since
        # Sprint 6/9 but never queried until now. Scoped per-project
        # (not global) and to ACTIVE assets only, so archiving an asset
        # clears the way for a fresh re-upload.
        duplicate = await self._repository.find_active_by_checksum(project_id, checksum)
        if duplicate is not None:
            raise DuplicateAssetError(duplicate)

        storage_path = await self._storage.save(
            project_id=project_id, filename=file_name, content=content
        )

        asset = Asset(
            project_id=project_id,
            owner_id=current_user_id,
            title=title or Path(file_name).stem or "Untitled",
            description=description,
            asset_type=asset_type or AssetType.OTHER,
            status=AssetStatus.ACTIVE,
            mime_type=mime_type,
            file_name=file_name,
            file_extension=extension,
            file_size=len(content),
            storage_path=storage_path,
            checksum=checksum,
            source=AssetSource.UPLOAD,
            version=1,
            tags=tags,
            asset_metadata={},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=current_user_id,
            processing_status=AssetProcessingStatus.QUEUED,
        )

        try:
            created = await self._repository.create(asset)
        except Exception:
            # Roll back the file write if the DB insert fails, so storage
            # and the database never drift out of sync.
            await self._storage.delete(storage_path)
            raise

        await self._session.commit()
        logger.info(
            "asset_uploaded",
            asset_id=str(created.id),
            project_id=str(project_id),
            owner_id=str(current_user_id),
            file_name=created.file_name,
            file_size=created.file_size,
            mime_type=created.mime_type,
        )

        # Enqueue only after the commit succeeds: a worker picking this
        # up must be able to find the row it's processing. `.delay()`
        # only publishes a message to the broker and returns
        # immediately — it does not run the task in this process, so
        # the file is never processed synchronously here.
        async_result = process_uploaded_asset.delay(str(created.id))
        logger.info(
            "celery_task_dispatched",
            task_name=process_uploaded_asset.name,
            asset_id=str(created.id),
        )
        logger.info(
            "celery_task_id_returned",
            task_id=async_result.id,
            asset_id=str(created.id),
        )
        return created

    async def create_imported_asset(
        self,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        content: bytes,
        file_name: str,
        mime_type: str,
        title: str,
        asset_type: AssetType = AssetType.DOCUMENT,
    ) -> Asset:
        """Create an asset from already-downloaded bytes (e.g. an
        imported OpenAlex PDF), mirroring `upload()`'s validation and
        dedup logic without the `UploadFile`-specific parts.

        Deliberately does NOT enqueue `process_uploaded_asset.delay()`:
        the caller (a paper-import Celery task) runs
        `AssetProcessingService.process_asset` inline immediately after
        this returns, so it can observe the final `processing_status`
        before the import chord's callback decides whether to start a
        re-run. Enqueuing here too would run the pipeline twice.
        """
        await self._ensure_project_owned(owner_id, project_id)

        file_name = sanitize_filename(file_name)
        extension = validate_extension(file_name)
        validate_extension_matches_mime(extension, mime_type)
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        validate_file_size(len(content), max_bytes=max_bytes)
        validate_content_matches_mime(content, mime_type)

        checksum = hashlib.sha256(content).hexdigest()
        duplicate = await self._repository.find_active_by_checksum(project_id, checksum)
        if duplicate is not None:
            raise DuplicateAssetError(duplicate)

        storage_path = await self._storage.save(
            project_id=project_id, filename=file_name, content=content
        )

        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title=title,
            description=None,
            asset_type=asset_type,
            status=AssetStatus.ACTIVE,
            mime_type=mime_type,
            file_name=file_name,
            file_extension=extension,
            file_size=len(content),
            storage_path=storage_path,
            checksum=checksum,
            source=AssetSource.IMPORTED,
            version=1,
            tags=[],
            asset_metadata={},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.QUEUED,
        )

        try:
            created = await self._repository.create(asset)
        except Exception:
            await self._storage.delete(storage_path)
            raise

        await self._session.commit()
        logger.info(
            "asset_imported",
            asset_id=str(created.id),
            project_id=str(project_id),
            owner_id=str(owner_id),
            file_name=created.file_name,
            file_size=created.file_size,
        )
        return created

    async def get_owned(self, current_user_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        """Fetch an asset, ensuring it belongs to the given user."""
        asset = await self._repository.get_by_id(asset_id)
        if asset is None or asset.owner_id != current_user_id:
            raise AssetNotFoundError(asset_id)
        return asset

    async def list_assets(
        self,
        current_user_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        """List the current user's assets, optionally scoped to one project."""
        if project_id is not None:
            await self._ensure_project_owned(current_user_id, project_id)
        return await self._repository.search(
            current_user_id, project_id=project_id, skip=skip, limit=limit
        )

    async def search(
        self,
        current_user_id: uuid.UUID,
        *,
        query: str | None,
        project_id: uuid.UUID | None,
        asset_type: AssetType | None,
        status: AssetStatus | None,
        tags: list[str] | None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        """Search the current user's assets by text, project, type, status, or tags."""
        if project_id is not None:
            await self._ensure_project_owned(current_user_id, project_id)
        return await self._repository.search(
            current_user_id,
            query=query,
            project_id=project_id,
            asset_type=asset_type,
            status=status,
            tags=tags,
            skip=skip,
            limit=limit,
        )

    async def update(
        self, current_user_id: uuid.UUID, asset_id: uuid.UUID, data: AssetUpdate
    ) -> Asset:
        """Apply a partial metadata update to an asset owned by the given user."""
        asset = await self.get_owned(current_user_id, asset_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(asset, _ORM_FIELD_ALIASES.get(field, field), value)
        await self._session.commit()
        await self._session.refresh(asset)
        return asset

    async def delete(self, current_user_id: uuid.UUID, asset_id: uuid.UUID) -> None:
        """Delete an asset owned by the given user, including its stored file."""
        asset = await self.get_owned(current_user_id, asset_id)
        storage_path = asset.storage_path
        await self._repository.delete(asset)
        await self._session.commit()
        # Delete the file only after the DB row is committed: an orphaned
        # file is harmless, but a DB row pointing at a missing file isn't.
        await self._storage.delete(storage_path)

    async def get_file_for_download(
        self, current_user_id: uuid.UUID, asset_id: uuid.UUID
    ) -> tuple[Asset, bytes]:
        """Fetch an owned asset's record and its raw file content."""
        asset = await self.get_owned(current_user_id, asset_id)
        content = await self._storage.read(asset.storage_path)
        return asset, content

    async def reprocess(
        self, current_user_id: uuid.UUID, asset_id: uuid.UUID, *, force: bool = False
    ) -> Asset:
        """Re-queue an owned asset for the extract/chunk pipeline.

        Sets `processing_status=QUEUED` immediately so the response
        reflects reality, then enqueues the same Celery task the
        upload path uses -- unless the asset already completed
        extraction, Qwen understanding, AND embedding successfully on
        its last run (Sprint 12.8: `_already_fully_processed`) and the
        caller did not ask to `force` a fresh run. Re-running the
        pipeline against the same stored bytes would reproduce
        identical chunks and pay the full Qwen/embedding cost again
        for no different result; a genuinely different file is a new
        upload (and a new asset), not a reprocess of this one.

        `force=True` always re-queues regardless of current state --
        the explicit escape hatch for "run it again anyway" (e.g. after
        a pipeline/model upgrade), matching this endpoint's existing
        documented purpose. A `FAILED`/partial/incomplete asset is
        never skipped, forced or not.
        """
        asset = await self.get_owned(current_user_id, asset_id)

        if not force and await self._already_fully_processed(asset):
            logger.info(
                "asset_reprocess_skipped_already_complete",
                asset_id=str(asset_id),
            )
            return asset

        asset.processing_status = AssetProcessingStatus.QUEUED
        asset.processing_error = None
        await self._session.commit()
        await self._session.refresh(asset)
        process_uploaded_asset.delay(str(asset.id))
        return asset

    async def _already_fully_processed(self, asset: Asset) -> bool:
        """Whether `asset` completed extraction, Qwen understanding, AND
        embedding on its last run, so reprocessing it now would repeat
        identical work against identical stored content.

        Deliberately conservative: every layer must show a genuine
        success, or this returns `False` and reprocessing proceeds
        normally. A prior `FAILED`/`UNAVAILABLE` AI-understanding
        result, any chunk not yet (or no longer) `COMPLETED`, or zero
        chunks at all -- each on its own is enough to force a real
        reprocess. Chunk completeness is checked by count, not by
        fetching every row, so this holds for an asset with any number
        of chunks.
        """
        if asset.processing_status is not AssetProcessingStatus.COMPLETED:
            return False

        profile = AIProfile.model_validate(asset.ai_profile or {})
        if profile.status is not AIProfileStatus.COMPLETED:
            return False

        total = await self._session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.asset_id == asset.id)
        )
        if not total:
            return False

        completed = await self._session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.asset_id == asset.id,
                KnowledgeChunk.embedding_status == EmbeddingStatus.COMPLETED,
            )
        )
        return completed == total


async def get_asset_service(
    session: AsyncSession = Depends(get_db),
    storage: StorageProvider = Depends(get_storage_provider),
) -> AssetService:
    """FastAPI dependency provider for `AssetService`."""
    return AssetService(session, storage)
