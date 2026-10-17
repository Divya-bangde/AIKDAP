"""Data-access layer for the `Asset` model.

Contains only persistence operations; transaction boundaries (commit)
and ownership/business rules live in `service.py`.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.enums import AssetStatus, AssetType
from app.modules.assets.models import Asset


class AssetRepository:
    """Encapsulates all direct database access for `Asset` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        """Fetch an asset by primary key, or None if not found."""
        return await self._session.get(Asset, asset_id)

    async def search(
        self,
        owner_id: uuid.UUID,
        *,
        query: str | None = None,
        project_id: uuid.UUID | None = None,
        asset_type: AssetType | None = None,
        status: AssetStatus | None = None,
        tags: list[str] | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        """List/search assets owned by a user, with optional filters."""
        stmt = select(Asset).where(Asset.owner_id == owner_id)

        if project_id is not None:
            stmt = stmt.where(Asset.project_id == project_id)
        if asset_type is not None:
            stmt = stmt.where(Asset.asset_type == asset_type)
        if status is not None:
            stmt = stmt.where(Asset.status == status)
        if tags:
            stmt = stmt.where(Asset.tags.overlap(tags))
        if query:
            like_pattern = f"%{query}%"
            stmt = stmt.where(
                or_(Asset.title.ilike(like_pattern), Asset.description.ilike(like_pattern))
            )

        stmt = stmt.order_by(Asset.created_at.desc(), Asset.id.desc()).offset(skip).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_active_by_checksum(
        self, project_id: uuid.UUID, checksum: str
    ) -> Asset | None:
        """Find an ACTIVE asset in this project with identical content.

        Scoped to one project (the same file in two different projects
        is not a duplicate -- each project's knowledge base is
        independent) and to `ACTIVE` status (an archived asset does not
        block a fresh re-upload).
        """
        stmt = select(Asset).where(
            Asset.project_id == project_id,
            Asset.checksum == checksum,
            Asset.status == AssetStatus.ACTIVE,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def find_by_execution_attempt(self, attempt_id: uuid.UUID) -> Asset | None:
        """Find the result `Asset` already persisted for one execution
        attempt, if any (Sprint 16 Phase 7B.29).

        `execution_launcher.launcher._persist_result_asset` stamps
        `asset_metadata["execution_attempt_id"]` on every result it
        creates -- this is the read half of that same convention, queried
        via Postgres JSONB's `->>'` text-extraction operator
        (`asset_metadata["execution_attempt_id"].astext`). It is what
        makes Docker-aware reconciliation's result recovery idempotent
        without a new schema/migration: a caller finalizing an attempt
        checks here FIRST, and only creates a new `Asset` if this returns
        `None` -- see `execution_launcher.launcher._finalize_exited_attempt`
        for the concurrency argument that makes this race-safe under two
        simultaneous reconcilers, not just single-caller-idempotent.
        """
        stmt = select(Asset).where(
            Asset.asset_metadata["execution_attempt_id"].astext == str(attempt_id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create(self, asset: Asset) -> Asset:
        """Insert a new asset row and flush to populate generated fields."""
        self._session.add(asset)
        await self._session.flush()
        await self._session.refresh(asset)
        return asset

    async def delete(self, asset: Asset) -> None:
        """Delete an asset row."""
        await self._session.delete(asset)
        await self._session.flush()
