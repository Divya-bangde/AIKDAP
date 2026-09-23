"""Data access for `paper_references`."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.enums import AssetStatus
from app.modules.assets.models import Asset
from app.modules.papers.models import PaperReference


class PaperReferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_asset(self, asset_id: uuid.UUID) -> PaperReference | None:
        result = await self._session.execute(
            select(PaperReference).where(PaperReference.asset_id == asset_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, asset_id: uuid.UUID, project_id: uuid.UUID) -> PaperReference:
        existing = await self.get_by_asset(asset_id)
        if existing is not None:
            return existing
        reference = PaperReference(asset_id=asset_id, project_id=project_id)
        self._session.add(reference)
        await self._session.flush()
        return reference

    async def list_by_project(self, project_id: uuid.UUID) -> list[PaperReference]:
        result = await self._session.execute(
            select(PaperReference).where(PaperReference.project_id == project_id)
        )
        return list(result.scalars().all())

    async def list_project_pdfs(
        self, project_id: uuid.UUID
    ) -> list[tuple[Asset, PaperReference | None]]:
        """Every active PDF in the project with its reference row, if any.

        Outer-joined from the asset side: a PDF whose lookup has not run
        yet has no row, and still has to be listed as unmatched.
        """
        result = await self._session.execute(
            select(Asset, PaperReference)
            .outerjoin(PaperReference, PaperReference.asset_id == Asset.id)
            .where(
                Asset.project_id == project_id,
                Asset.mime_type == "application/pdf",
                Asset.status == AssetStatus.ACTIVE,
            )
            .order_by(Asset.created_at)
        )
        return [(row[0], row[1]) for row in result.all()]
