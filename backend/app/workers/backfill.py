"""Backfill the visualization data for records saved before it existed.

Three independent passes, each idempotent, batched and resumable:

- `chunk_positions` for completed PDF assets whose chunks have none;
- `paper_references` for PDF assets with no row, or one still `pending`;
- `source_mix` for completed research runs and generated report assets
  that have none.

Resumable because every pass selects only records still missing their
data, walking them by id (keyset), so an interrupted run simply picks up
where the data is still missing, and a record that fails is skipped
rather than retried forever within one pass. Step events are not
backfilled: that data was never recorded.

Driven by the `workers.backfill_visualization_data` Celery task and by
`scripts/backfill_visualization.py`.
"""

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import exists, or_, select

from app.core.logging.logger import get_logger
from app.database.session import open_session
from app.modules.assets.enums import AssetProcessingStatus, AssetSource
from app.modules.assets.models import Asset
from app.modules.assets.processing.positions import store_chunk_positions
from app.modules.assets.storage import get_storage_provider
from app.modules.knowledge_base.models import ChunkPosition, KnowledgeChunk
from app.modules.papers.models import OpenAlexStatus, PaperReference
from app.modules.papers.service import PDF_MIME, PaperReferenceService
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
from app.modules.research.source_mix import compute_report_source_mix, compute_run_source_mix

logger = get_logger(__name__)

_NIL = uuid.UUID(int=0)


@dataclass
class BackfillReport:
    positions_assets: int = 0
    paper_lookups: int = 0
    run_source_mix: int = 0
    asset_source_mix: int = 0
    failures: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


async def backfill_chunk_positions(report: BackfillReport, batch_size: int) -> None:
    last_id = _NIL
    missing = (
        select(KnowledgeChunk.id)
        .where(KnowledgeChunk.asset_id == Asset.id)
        .where(~exists().where(ChunkPosition.chunk_id == KnowledgeChunk.id))
    )
    while True:
        async with open_session() as session:
            assets = list(
                (
                    await session.execute(
                        select(Asset)
                        .where(
                            Asset.id > last_id,
                            Asset.mime_type == PDF_MIME,
                            Asset.processing_status == AssetProcessingStatus.COMPLETED,
                            exists(missing),
                        )
                        .order_by(Asset.id)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not assets:
                return
            storage = get_storage_provider()
            for asset in assets:
                last_id = asset.id
                chunks = list(
                    (
                        await session.execute(
                            select(KnowledgeChunk).where(
                                KnowledgeChunk.asset_id == asset.id,
                                ~exists().where(ChunkPosition.chunk_id == KnowledgeChunk.id),
                            )
                        )
                    ).scalars()
                )
                try:
                    content = await storage.read(asset.storage_path)
                    await store_chunk_positions(asset.id, content, chunks)
                    report.positions_assets += 1
                except Exception as exc:  # noqa: BLE001 - skip and continue
                    report.failures += 1
                    logger.warning(
                        "backfill_positions_failed", asset_id=str(asset.id), error_type=type(exc).__name__
                    )
        logger.info("backfill_positions_progress", assets=report.positions_assets, last_id=str(last_id))


async def backfill_paper_references(report: BackfillReport, batch_size: int) -> None:
    last_id = _NIL
    while True:
        async with open_session() as session:
            asset_ids = list(
                (
                    await session.execute(
                        select(Asset.id)
                        .outerjoin(PaperReference, PaperReference.asset_id == Asset.id)
                        .where(
                            Asset.id > last_id,
                            Asset.mime_type == PDF_MIME,
                            Asset.source != AssetSource.GENERATED,
                            Asset.processing_status == AssetProcessingStatus.COMPLETED,
                            or_(
                                PaperReference.id.is_(None),
                                PaperReference.openalex_status == OpenAlexStatus.PENDING.value,
                            ),
                        )
                        .order_by(Asset.id)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not asset_ids:
                return
            service = PaperReferenceService(session, get_storage_provider())
            for asset_id in asset_ids:
                last_id = asset_id
                try:
                    await service.lookup(asset_id)
                    report.paper_lookups += 1
                except Exception as exc:  # noqa: BLE001 - left pending, skipped
                    await session.rollback()
                    report.failures += 1
                    logger.warning(
                        "backfill_paper_lookup_failed", asset_id=str(asset_id), error_type=type(exc).__name__
                    )
        logger.info("backfill_papers_progress", lookups=report.paper_lookups, last_id=str(last_id))


async def backfill_source_mix(report: BackfillReport, batch_size: int) -> None:
    last_id = _NIL
    while True:
        async with open_session() as session:
            runs = list(
                (
                    await session.execute(
                        select(ResearchRun)
                        .where(
                            ResearchRun.id > last_id,
                            ResearchRun.status == ResearchRunStatus.COMPLETED,
                            ResearchRun.source_mix.is_(None),
                        )
                        .order_by(ResearchRun.id)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not runs:
                break
            for run in runs:
                last_id = run.id
                run.source_mix = compute_run_source_mix(
                    run.final_answer,
                    run.citations,
                    run.grounding_status.value if run.grounding_status else None,
                )
            await session.commit()
            report.run_source_mix += len(runs)
        logger.info("backfill_run_source_mix_progress", runs=report.run_source_mix)

    last_id = _NIL
    while True:
        async with open_session() as session:
            assets = list(
                (
                    await session.execute(
                        select(Asset)
                        .where(
                            Asset.id > last_id,
                            Asset.source == AssetSource.GENERATED,
                            Asset.processing_status == AssetProcessingStatus.COMPLETED,
                            Asset.source_mix.is_(None),
                        )
                        .order_by(Asset.id)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not assets:
                return
            for asset in assets:
                last_id = asset.id
                asset.source_mix = compute_report_source_mix(
                    (asset.asset_metadata or {}).get("sections")
                )
            await session.commit()
            report.asset_source_mix += len(assets)
        logger.info("backfill_asset_source_mix_progress", assets=report.asset_source_mix)


async def run_backfill(
    *, batch_size: int = 50, positions: bool = True, papers: bool = True, source_mix: bool = True
) -> BackfillReport:
    report = BackfillReport()
    if source_mix:
        await backfill_source_mix(report, batch_size)
    if positions:
        await backfill_chunk_positions(report, batch_size)
    if papers:
        await backfill_paper_references(report, batch_size)
    logger.info("backfill_completed", **report.as_dict())
    return report
