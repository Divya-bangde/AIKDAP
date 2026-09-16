"""Data-access layer for the reports module.

Contains only persistence operations, matching the existing convention
(`app.modules.assets.repository`): ownership and business rules live in
`service.py`.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.enums import AssetProcessingStatus, AssetType
from app.modules.assets.models import Asset


class ReportRepository:
    """Queries over `Asset` rows specific to report generation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_processed_documents(self, project_id: uuid.UUID) -> list[Asset]:
        """The project's own documents a report may draw on: `DOCUMENT`
        assets (covers both uploads and OpenAlex-imported papers --
        `AssetService.create_imported_asset` also defaults to
        `AssetType.DOCUMENT`) that finished the extract/chunk pipeline.

        Deliberately excludes `GENERATED`/`SUMMARY`/`REPORT` assets --
        a prior synopsis must never become a source for the next one,
        or "references built only from your documents" would quietly
        start citing AIKDAP's own output.
        """
        stmt = (
            select(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.asset_type == AssetType.DOCUMENT,
                Asset.processing_status == AssetProcessingStatus.COMPLETED,
            )
            .order_by(Asset.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def claim_pending(self, asset_id: uuid.UUID) -> bool:
        """Atomically move a report from `pending` to `running`.

        One conditional UPDATE, so exactly one caller can win: a
        duplicate or redelivered Celery message finds the row no longer
        `pending` and gets `False` -- before any LLM call or write. The
        caller commits."""
        result = await self._session.execute(
            update(Asset)
            .where(
                Asset.id == asset_id,
                Asset.processing_status == AssetProcessingStatus.PENDING,
            )
            .values(processing_status=AssetProcessingStatus.RUNNING)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def list_processed_documents_by_ids(
        self, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> list[Asset]:
        """The subset of `list_processed_documents` the caller selected.

        Filtered in SQL rather than in Python so a selection naming a
        thousand ids does not load the whole project. An empty
        `asset_ids` returns nothing: "select none" is never "select
        all" -- the service rejects an empty selection with a 422 before
        this is reached, and this method must not silently disagree.
        """
        if not asset_ids:
            return []
        stmt = (
            select(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.id.in_(asset_ids),
                Asset.asset_type == AssetType.DOCUMENT,
                Asset.processing_status == AssetProcessingStatus.COMPLETED,
            )
            .order_by(Asset.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_project_asset_ids(self, project_id: uuid.UUID) -> set[uuid.UUID]:
        """Every asset id in this project, whatever its type or status.

        The ownership check for a build-plan selection, and deliberately
        broader than `list_processed_documents_by_ids`: "this id is not in
        your project" (a `404`) and "this id is in your project but is not
        a processed document" (a `422`) are different answers, so the
        service needs both sets rather than inferring one from the other.

        `AssetRepository` has no project-wide listing method -- its
        `search` is a paged, filtered query for the assets API -- so this
        lives here rather than widening that one for a membership test.
        """
        result = await self._session.execute(
            select(Asset.id).where(Asset.project_id == project_id)
        )
        return set(result.scalars().all())
