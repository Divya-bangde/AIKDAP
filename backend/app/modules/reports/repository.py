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
