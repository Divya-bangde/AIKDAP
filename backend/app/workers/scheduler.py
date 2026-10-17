"""Manually invoked maintenance tasks, including durable embedding rebuilds.

None of these run automatically — `celery_app.py` defines no
`beat_schedule`, so nothing here is on an active cron. They're
registered, individually invokable tasks (e.g. `celery -A
app.workers.worker call workers.retry_failed_tasks`), ready for a
future sprint to schedule once the corresponding policy is actually
decided (how often, what a "temp file" even means for whichever
storage backend is active, etc.) — decisions out of scope here.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.core.logging.logger import get_logger
from app.database.session import async_session_factory
from app.modules.assets.enums import AssetProcessingStatus
from app.modules.assets.models import Asset
from app.workers.celery_app import celery_app
from app.workers.tasks import _run_task_loop, log_task_execution, process_uploaded_asset

logger = get_logger(__name__)


@celery_app.task(name="workers.retry_failed_tasks", bind=True, max_retries=1, default_retry_delay=60)
@log_task_execution
def retry_failed_tasks(self) -> dict[str, int]:
    """Re-enqueue every asset stuck at `processing_status=FAILED`.

    The one task here with real logic — a bounded, safe query-and-
    requeue against the existing pipeline (`process_uploaded_asset`),
    not a new capability. Idempotent to call repeatedly: a
    successfully reprocessed asset moves off `FAILED` and won't be
    picked up again next run.
    """
    asset_ids = asyncio.run(_find_failed_asset_ids())
    for asset_id in asset_ids:
        process_uploaded_asset.delay(str(asset_id))
    logger.info("retry_failed_tasks_requeued", count=len(asset_ids))
    return {"requeued_count": len(asset_ids)}


async def _find_failed_asset_ids() -> list[uuid.UUID]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Asset.id).where(Asset.processing_status == AssetProcessingStatus.FAILED)
        )
        return list(result.scalars().all())


@celery_app.task(name="workers.cleanup_temp_files", bind=True)
@log_task_execution
def cleanup_temp_files(self) -> dict[str, str]:
    """Placeholder: no-op today.

    `LocalStorageProvider` writes directly to its final path (see
    `app.modules.assets.storage`) — there is no "temp file" concept
    yet to clean up. Reserved for a future storage backend or upload
    flow that introduces one (e.g. multipart upload staging).
    """
    logger.info("cleanup_temp_files_placeholder", reason="No temp-file concept exists yet to clean up.")
    return {"status": "not_implemented"}


@celery_app.task(name="workers.rebuild_embeddings", bind=True, max_retries=3, default_retry_delay=30)
@log_task_execution
def rebuild_embeddings(self, rebuild_id: str) -> dict:
    """Resume a durable project rebuild; successful staged batches survive retries."""
    from app.modules.knowledge_base.rebuild import run_rebuild

    try:
        result = _run_task_loop(run_rebuild(async_session_factory, uuid.UUID(rebuild_id)))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    if result["status"] == "busy":
        # Worker death can redeliver before lease expiry; retry after that lease.
        raise self.retry(countdown=result["retry_after"])
    return result
