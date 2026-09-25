"""Business logic for business analytics. Ownership follows the reports
module: "not yours" and "doesn't exist" are the same NotFound."""

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics.dataset import DatasetProfile, build_profile, read_dataframe
from app.core.config.settings import settings
from app.database.session import get_db
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import StorageProvider, get_storage_provider
from app.modules.business_analytics.repository import ANALYSIS_TAG, AnalysisRepository, dataset_tag
from app.modules.business_analytics.schemas import KaggleImportRequest
from app.modules.projects.repository import ProjectRepository
from app.integrations.kaggle.client import KaggleFile, get_kaggle_client
from app.workers.tasks import import_kaggle_dataset, run_analysis

#: How many earlier completed analyses the planner sees.
HISTORY_SIZE = 3


class AnalyticsNotFoundError(Exception):
    """Asset missing or not owned by the caller."""


class DatasetNotReadyError(Exception):
    """The asset is not a dataset, or has not finished processing."""


class ProjectAccessDeniedError(Exception):
    """Project missing or not owned by the caller."""


class AnalyticsService:
    def __init__(self, session: AsyncSession, storage: StorageProvider) -> None:
        self._session = session
        self._storage = storage
        self._assets = AssetRepository(session)
        self._analyses = AnalysisRepository(session)

    async def _owned(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        asset = await self._assets.get_by_id(asset_id)
        if asset is None or asset.owner_id != owner_id:
            raise AnalyticsNotFoundError(asset_id)
        return asset

    async def get_dataset(self, owner_id: uuid.UUID, dataset_id: uuid.UUID) -> Asset:
        asset = await self._owned(owner_id, dataset_id)
        if asset.asset_type is not AssetType.DATASET or asset.processing_status is not AssetProcessingStatus.COMPLETED:
            raise DatasetNotReadyError(dataset_id)
        return asset

    async def get_profile(self, owner_id: uuid.UUID, dataset_id: uuid.UUID, sheet: str | None = None) -> DatasetProfile:
        """Cached on the dataset; computed on first request (first sheet only is cached)."""
        dataset = await self.get_dataset(owner_id, dataset_id)
        if sheet is None and "profile" in dataset.asset_metadata:
            return dataset.asset_metadata["profile"]
        content = await self._storage.read(dataset.storage_path)
        frame, truncated = read_dataframe(
            content, f".{dataset.file_extension.lstrip('.')}", sheet=sheet, max_rows=settings.analytics_max_rows
        )
        profile = build_profile(frame, truncated=truncated)
        if sheet is None:
            dataset.asset_metadata = {**dataset.asset_metadata, "profile": profile}
            await self._session.commit()
        return profile

    async def create_analysis(
        self, owner_id: uuid.UUID, dataset_id: uuid.UUID, question: str, sheet: str | None
    ) -> Asset:
        dataset = await self.get_dataset(owner_id, dataset_id)
        thread = await self._analyses.list_thread(dataset_id)
        history = [
            {"question": item.asset_metadata["analysis"]["question"], "plan": item.asset_metadata["analysis"]["plan"]}
            for item in thread
            if item.processing_status is AssetProcessingStatus.COMPLETED and item.asset_metadata["analysis"].get("plan")
        ][-HISTORY_SIZE:]
        asset = Asset(
            project_id=dataset.project_id,
            owner_id=owner_id,
            title=question[:250],
            description=None,
            asset_type=AssetType.CHART,
            status=AssetStatus.ACTIVE,
            # No file is stored: the analysis is rendered from asset_metadata.
            mime_type="application/json",
            file_name="analysis.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[ANALYSIS_TAG, dataset_tag(dataset_id)],
            asset_metadata={
                "analysis": {
                    "dataset_id": str(dataset_id), "question": question, "sheet": sheet,
                    "history": history, "plan": None, "result": None,
                    "narrative": None, "unverified_numbers": [],
                }
            },
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.PENDING,
        )
        created = await self._assets.create(asset)
        await self._session.commit()
        run_analysis.delay(str(created.id))
        return created

    async def list_analyses(self, owner_id: uuid.UUID, dataset_id: uuid.UUID) -> list[Asset]:
        await self._owned(owner_id, dataset_id)
        return await self._analyses.list_thread(dataset_id)

    async def get_analysis(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        asset = await self._owned(owner_id, asset_id)
        if ANALYSIS_TAG not in asset.tags:
            raise AnalyticsNotFoundError(asset_id)
        return asset

    async def list_kaggle_files(self, owner: str, dataset: str) -> list[KaggleFile]:
        return await get_kaggle_client().list_files(owner, dataset)

    async def import_from_kaggle(self, owner_id: uuid.UUID, project_id: uuid.UUID, request: KaggleImportRequest) -> None:
        project = await ProjectRepository(self._session).get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectAccessDeniedError(project_id)
        get_kaggle_client()  # fail fast with 503 before queueing
        import_kaggle_dataset.delay(str(project_id), str(owner_id), request.owner, request.dataset, request.file_name)


async def get_analytics_service(session: AsyncSession = Depends(get_db)) -> AnalyticsService:
    return AnalyticsService(session, get_storage_provider())
