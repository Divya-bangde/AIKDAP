"""Persistence queries for analysis assets."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.enums import AssetSource, AssetType
from app.modules.assets.models import Asset

ANALYSIS_TAG = "analysis"


def dataset_tag(dataset_id: uuid.UUID) -> str:
    return f"dataset:{dataset_id}"


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_thread(self, dataset_id: uuid.UUID) -> list[Asset]:
        """Every analysis of one dataset, oldest first."""
        result = await self._session.execute(
            select(Asset)
            .where(
                Asset.source == AssetSource.GENERATED,
                Asset.asset_type == AssetType.CHART,
                Asset.tags.contains([dataset_tag(dataset_id)]),
            )
            .order_by(Asset.created_at.asc())
        )
        return list(result.scalars().all())
