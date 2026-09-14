"""Celery tasks for the asset processing pipeline and research execution.

Celery task bodies are synchronous by design — the default `prefork`
worker pool doesn't support native `async def` tasks. Every task here
is a thin bridge that opens a fresh event loop via `asyncio.run()` to
drive the async DB/service layer for the duration of one task, then
returns. All real business logic lives in the existing processing
services (`AssetProcessingService`, `AssetRepository`,
`get_text_extractor`, `get_embedding_provider`) — nothing here
reimplements them; each task's body is a call into one.

`process_uploaded_asset` is the one actually wired up (enqueued by
`AssetService.upload` and `POST /assets/{id}/process`) and is the only
task whose failure path is "primary" — `AssetProcessingService`
records failures on the asset's own `processing_status`/
`processing_error` rather than raising, so this task's own retry
handling is a safety net for infrastructure failures, not the main
error-reporting mechanism.

`extract_document_text`, `generate_ai_metadata`, `generate_embeddings`,
and `update_processing_status` are standalone, individually invokable
steps — useful for future composition (e.g. a Celery `chain()`, or a
future Planner triggering just one phase) even though nothing calls
them yet. `generate_ai_metadata` (Sprint 9B, Qwen) and
`generate_embeddings` (Sprint 9C, BGE-M3) both run real local models
now; each shares its underlying service/provider with the inline
pipeline call in `AssetProcessingService.process_asset` rather than
reimplementing it — this file adds only the standalone-invocation
bridge.
"""

import asyncio
import functools
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from celery import chord, group

from app.core.config import settings
from app.core.llm import LLMError
from app.core.logging.logger import get_logger
from app.database.session import async_session_factory
from app.modules.assets.ai_profile import AIProfile, AIProfileStatus
from app.modules.assets.enums import AssetProcessingStatus, EmbeddingStatus
from app.modules.assets.processing.document_understanding import (
    DocumentUnderstandingError,
    get_document_understanding_service,
)
from app.modules.assets.processing.extractors import (
    ExtractionNotSupportedError,
    get_text_extractor,
)
from app.modules.assets.processing.pipeline import get_asset_processing_service
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import get_storage_provider
from app.modules.assets.validators import AssetValidationError
from app.modules.execution.repository import ExecutionJobRepository
from app.modules.execution.service import prepare_approved_launch, recover_interrupted_retry_attempt
from execution_launcher.launcher import execute_approved_launch, reconcile_attempt
from app.modules.knowledge_base.embeddings import get_embedding_provider
from app.modules.knowledge_base.repository import KnowledgeChunkRepository
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
from app.modules.research.paper_import import PaperDownloadError, download_oa_pdf
from app.modules.research.repository import ResearchRunRepository
from app.modules.research.service import ResearchExecutionService
from app.workers.celery_app import celery_app
from execution_launcher.models import InputResolutionError, SecurityBlocked

logger = get_logger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])


def log_task_execution(func: _F) -> _F:
    """Standardize the four log events every task must emit (started,
    completed, failed, execution time), so individual tasks only
    contain their own logic, not logging boilerplate.

    Expects to wrap a `bind=True` task body (`self` is the Celery
    `Task` instance, giving access to `self.name`/`self.request.id`).
    Re-raises on failure — logging here never swallows the exception,
    since Celery's own retry/failure bookkeeping depends on it
    propagating.

    `target_id` is the task's first positional argument by convention:
    the asset id for pipeline tasks, the run id for research execution.
    """

    @functools.wraps(func)
    def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        logger.info(
            "task_started", task=self.name, task_id=self.request.id, target_id=args[0] if args else None
        )
        try:
            result = func(self, *args, **kwargs)
        except Exception as exc:
            logger.error(
                "task_failed",
                task=self.name,
                task_id=self.request.id,
                duration_seconds=round(time.monotonic() - start, 3),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        logger.info(
            "task_completed",
            task=self.name,
            task_id=self.request.id,
            duration_seconds=round(time.monotonic() - start, 3),
        )
        return result

    return wrapper  # type: ignore[return-value]


def _run_task_loop(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Drive one Celery task body to completion, then reset LiteLLM's
    global logging worker before this loop closes (Sprint 9J).

    The single choke point every task below runs through instead of a
    bare `asyncio.run(...)`, so the fix lives in exactly one place
    rather than being repeated at each of the six call sites. The
    reset itself is `reset_litellm_logging_worker_for_task_boundary`
    in `app.core.llm.gateway` — kept there rather than here so this
    module never imports `litellm` directly, preserving the
    containment invariant `test_litellm_is_imported_only_by_the_gateway`
    checks (see that function's docstring for the full root-cause
    explanation of what is being reset and why).

    Sprint 16 Phase 8.0: this function calls a FRESH `asyncio.run()` for
    every task, and a long-lived worker process makes MANY such calls
    over its lifetime -- confirmed live, against a real worker consuming
    from a real Redis broker, that this breaks once a `pool_pre_ping`-
    backed connection pool is shared across them (`asyncpg` connections
    are bound to the loop that created them; a later task's `asyncio.
    run()` creates a new loop, and re-validating a pooled connection
    from a now-closed loop raises `RuntimeError: ... attached to a
    different loop`). An `await engine.dispose()` added here after every
    task was tried first and verified correct in an isolated repro, but
    the real worker still failed on a later task despite it -- see
    `app.database.session.configure_for_worker_process`'s own docstring
    for the full story. The actual fix lives THERE: `app.workers.worker`
    calls it before any task module is imported, rebuilding the engine
    with `NullPool` so no connection is ever held between checkouts in
    the first place -- nothing to do here as a result.
    """
    from app.core.llm.gateway import reset_litellm_logging_worker_for_task_boundary

    async def _wrapped() -> Any:
        try:
            return await coroutine
        finally:
            await reset_litellm_logging_worker_for_task_boundary()

    return asyncio.run(_wrapped())


@celery_app.task(name="workers.process_uploaded_asset", bind=True, max_retries=3, default_retry_delay=30)
@log_task_execution
def process_uploaded_asset(self, asset_id: str) -> dict[str, str]:
    """Run the full extract -> chunk -> (future) embed pipeline for one
    asset. This is the task actually enqueued after upload/reprocess."""
    try:
        _run_task_loop(_run_pipeline(uuid.UUID(asset_id)))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return {"status": "ok", "asset_id": asset_id}


async def _run_pipeline(asset_id: uuid.UUID) -> None:
    async with async_session_factory() as session:
        storage = get_storage_provider()
        service = get_asset_processing_service(session, storage)
        await service.process_asset(asset_id)


@celery_app.task(name="workers.extract_document_text", bind=True, max_retries=3, default_retry_delay=30)
@log_task_execution
def extract_document_text(self, asset_id: str) -> dict[str, str | int]:
    """Extract raw text for one asset in isolation, without running
    chunking or touching `processing_status`. A standalone step for
    future composition; `process_uploaded_asset` runs the same
    extractor internally as part of the full pipeline today."""
    try:
        text = _run_task_loop(_extract_text(uuid.UUID(asset_id)))
    except ExtractionNotSupportedError as exc:
        # Not retried: an unsupported MIME type won't become supported
        # by waiting, unlike a transient DB/network failure.
        logger.info("extract_document_text_unsupported", asset_id=asset_id, reason=str(exc))
        return {"status": "unsupported", "asset_id": asset_id, "reason": str(exc)}
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return {"status": "ok", "asset_id": asset_id, "character_count": len(text)}


async def _extract_text(asset_id: uuid.UUID) -> str:
    async with async_session_factory() as session:
        asset = await AssetRepository(session).get_by_id(asset_id)
        if asset is None:
            raise ValueError(f"Asset {asset_id} not found.")
        storage = get_storage_provider()
        content = await storage.read(asset.storage_path)
        extractor = get_text_extractor(asset.mime_type)
        return await extractor.extract(content)


@celery_app.task(name="workers.generate_ai_metadata", bind=True, max_retries=3, default_retry_delay=30)
@log_task_execution
def generate_ai_metadata(self, asset_id: str) -> dict[str, str]:
    """Run local Qwen document understanding for one asset in isolation.

    Standalone step for future composition (a Celery `chain()`, or a
    caller that wants to re-run just this stage) — the automatic
    pipeline (`process_uploaded_asset` -> `AssetProcessingService.
    process_asset`) already runs the same underlying
    `QwenDocumentUnderstandingService` inline after extraction
    succeeds. Both entry points call the one implementation; nothing
    is duplicated between them.

    Requires the asset to already have extracted text available (i.e.
    `processing_status` has reached at least `CHUNKING`/`COMPLETED`);
    this task does not run extraction itself.
    """
    try:
        result = _run_task_loop(_generate_ai_metadata(uuid.UUID(asset_id)))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return {"asset_id": asset_id, **result}


async def _generate_ai_metadata(asset_id: uuid.UUID) -> dict[str, str]:
    async with async_session_factory() as session:
        repository = AssetRepository(session)
        asset = await repository.get_by_id(asset_id)
        if asset is None:
            raise ValueError(f"Asset {asset_id} not found.")

        storage = get_storage_provider()
        content = await storage.read(asset.storage_path)
        extractor = get_text_extractor(asset.mime_type)
        # `extract()` returns an `ExtractedDocument`, not a plain string --
        # `.full_text` is what `QwenDocumentUnderstandingService.analyze`
        # (which calls `.strip()` on its input) actually needs. This task
        # went uncalled from Sprint 9B until Phase 8.11 Part D enqueued it
        # for the first time, which is when this mismatch first surfaced.
        extracted = await extractor.extract(content)

        profile = AIProfile.model_validate(asset.ai_profile or {})
        understanding = get_document_understanding_service()
        try:
            metadata = await understanding.analyze(extracted.full_text)
        except DocumentUnderstandingError as exc:
            profile.status = AIProfileStatus.FAILED
            profile.error = str(exc)
            status = "failed"
        except LLMError as exc:
            profile.status = AIProfileStatus.UNAVAILABLE
            profile.error = str(exc)
            status = "unavailable"
        else:
            profile.summary = metadata.summary
            profile.keywords = metadata.keywords
            profile.entities = metadata.entities
            profile.topics = metadata.topics
            profile.language = metadata.language
            profile.generated_by = settings.qwen_model
            profile.status = AIProfileStatus.COMPLETED
            profile.truncated = metadata.truncated
            profile.processed_sections = metadata.processed_sections
            profile.total_sections = metadata.total_sections
            profile.error = None
            status = "completed"

        asset.ai_profile = profile.model_dump(mode="json")
        await session.commit()
        return {"status": status}


@celery_app.task(name="workers.generate_embeddings", bind=True, max_retries=3, default_retry_delay=30)
@log_task_execution
def generate_embeddings(self, asset_id: str) -> dict[str, str]:
    """Generate BGE-M3 embeddings for one asset's existing knowledge
    chunks, in isolation.

    Standalone step for future composition — the automatic pipeline
    (`process_uploaded_asset` -> `AssetProcessingService.
    process_asset`) already runs the same `EmbeddingProvider.embed()`
    call inline, right after chunking. Both entry points call the one
    provider from `get_embedding_provider()`; nothing is duplicated.

    Requires the asset to already have chunks (i.e. extraction/chunking
    has already run); this task does not extract or chunk.
    """
    try:
        result = _run_task_loop(_generate_embeddings(uuid.UUID(asset_id)))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return {"asset_id": asset_id, **result}


async def _generate_embeddings(asset_id: uuid.UUID) -> dict[str, str]:
    async with async_session_factory() as session:
        asset = await AssetRepository(session).get_by_id(asset_id)
        if asset is None:
            raise ValueError(f"Asset {asset_id} not found.")

        chunks = await KnowledgeChunkRepository(session).list_by_project(
            asset.project_id, asset_id=asset_id
        )
        if not chunks:
            return {"status": "no_chunks", "chunk_count": "0"}

        provider = get_embedding_provider()
        for chunk in chunks:
            chunk.embedding_status = EmbeddingStatus.PROCESSING
        await session.commit()

        try:
            vectors = await provider.embed([chunk.content for chunk in chunks])
        except Exception as exc:
            for chunk in chunks:
                chunk.embedding_status = EmbeddingStatus.FAILED
            await session.commit()
            logger.warning(
                "generate_embeddings_failed",
                asset_id=str(asset_id),
                chunk_count=len(chunks),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return {"status": "failed", "chunk_count": str(len(chunks))}

        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_status = EmbeddingStatus.COMPLETED
            chunk.embedding_provider = provider.name
        await session.commit()
        return {"status": "completed", "chunk_count": str(len(chunks)), "provider": provider.name.value}


@celery_app.task(name="workers.update_processing_status", bind=True, max_retries=3, default_retry_delay=10)
@log_task_execution
def update_processing_status(self, asset_id: str, status: str, error: str | None = None) -> dict[str, str]:
    """Update an asset's `processing_status`/`processing_error` as a
    standalone step, outside the main pipeline — for a future task
    that reports progress without running the full
    `AssetProcessingService.process_asset` flow."""
    try:
        _run_task_loop(_update_status(uuid.UUID(asset_id), AssetProcessingStatus(status), error))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return {"status": "ok", "asset_id": asset_id, "processing_status": status}


async def _update_status(asset_id: uuid.UUID, status: AssetProcessingStatus, error: str | None) -> None:
    async with async_session_factory() as session:
        asset = await AssetRepository(session).get_by_id(asset_id)
        if asset is None:
            raise ValueError(f"Asset {asset_id} not found.")
        asset.processing_status = status
        asset.processing_error = error
        await session.commit()


@celery_app.task(name="workers.execute_research_run", bind=True, max_retries=3, default_retry_delay=30)
@log_task_execution
def execute_research_run(self, run_id: str, workspace_context: dict[str, Any] | None = None) -> dict[str, str]:
    """Execute the LangGraph research workflow for one research run.

    Enqueued by `ResearchService.start_run` so `POST /research/run` can
    return `201` without waiting for any part of the workflow. The same
    thin-bridge pattern as `process_uploaded_asset`: all logic lives in
    `ResearchExecutionService`, and the run row — not Celery's result
    backend — is the source of truth for whether it succeeded.

    Retries cover infrastructure failures only. A workflow failure is
    recorded on the run (`status=failed`, `error_message=...`) and does
    not raise, since retrying a deterministic planning/synthesis error
    would fail identically three more times.
    """
    try:
        _run_task_loop(_run_research(uuid.UUID(run_id), workspace_context))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    return {"status": "ok", "run_id": run_id}


async def _run_research(run_id: uuid.UUID, workspace_context: dict[str, Any] | None = None) -> None:
    async with async_session_factory() as session:
        await ResearchExecutionService(session).execute(run_id, workspace_context)


# ---------------------------------------------------------------------------
# Milestone 10 step 3: Add & re-run
#
# One `import_suggested_paper` task per selected paper (download ->
# existing upload validators -> Asset(source=IMPORTED) -> existing
# extract/chunk/embed pipeline, run inline so this task can observe the
# final `processing_status`), fed into a `chord` whose callback
# (`finalize_paper_import`) starts exactly one re-run once every paper
# has reached a final state -- and only if at least one succeeded.
# ---------------------------------------------------------------------------


def dispatch_paper_import(run_id: str, papers: list[dict[str, Any]]) -> None:
    """Fire the import chord for one run's selected papers.

    The only place `celery.chord`/`celery.group` are constructed --
    `ResearchService.import_papers` calls this rather than importing
    Celery primitives itself, the same separation `start_run` keeps by
    importing `execute_research_run` locally instead of calling
    `.delay()` on something it constructs.
    """
    chord(group(import_suggested_paper.s(run_id, paper) for paper in papers))(
        finalize_paper_import.s(run_id)
    )


@celery_app.task(name="workers.import_suggested_paper", bind=True, max_retries=0)
@log_task_execution
def import_suggested_paper(self, run_id: str, paper: dict[str, Any]) -> dict[str, Any]:
    """Download, validate, and process one OpenAlex-suggested paper as a
    project asset.

    Never retries and never raises for a paper-specific failure (bad
    link, not a PDF, too large, extraction failed): each is a normal
    outcome for one paper among several selected, recorded on
    `run.suggested_papers[].import_status` and returned so the chord's
    callback can still start a re-run with the papers that succeeded.
    """
    return _run_task_loop(_import_paper(uuid.UUID(run_id), paper))


async def _import_paper(run_id: uuid.UUID, paper: dict[str, Any]) -> dict[str, Any]:
    # Imported locally, not at module scope: `app.modules.assets.service`
    # itself imports `process_uploaded_asset` from this module at import
    # time, so a top-level `from app.modules.assets.service import
    # AssetService` here would form a circular import. Same fix already
    # used by `_run_task_loop`'s local import of `app.core.llm.gateway`.
    from app.modules.assets.service import AssetService, DuplicateAssetError

    openalex_id = paper["openalex_id"]
    async with async_session_factory() as session:
        await _update_paper_import_status(session, run_id, openalex_id, status="processing")

        try:
            content = await download_oa_pdf(
                paper["oa_pdf_url"],
                timeout=settings.paper_import_download_timeout,
                max_bytes=settings.max_upload_size_mb * 1024 * 1024,
                max_redirects=settings.paper_import_max_redirects,
            )
        except PaperDownloadError as exc:
            await _update_paper_import_status(
                session, run_id, openalex_id, status="failed", error=str(exc)
            )
            return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": str(exc)}

        run = await ResearchRunRepository(session).get_by_id(run_id)
        if run is None:
            reason = "The research run no longer exists."
            await _update_paper_import_status(session, run_id, openalex_id, status="failed", error=reason)
            return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": reason}

        storage = get_storage_provider()
        file_name = f"{openalex_id.rsplit('/', 1)[-1]}.pdf"
        try:
            asset = await AssetService(session, storage).create_imported_asset(
                owner_id=run.owner_id,
                project_id=run.project_id,
                content=content,
                file_name=file_name,
                mime_type="application/pdf",
                title=paper.get("title") or file_name,
            )
        except (AssetValidationError, DuplicateAssetError) as exc:
            await _update_paper_import_status(
                session, run_id, openalex_id, status="failed", error=str(exc)
            )
            return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": str(exc)}

        await get_asset_processing_service(session, storage).process_asset(asset.id)
        await session.refresh(asset)

        if asset.processing_status is AssetProcessingStatus.COMPLETED:
            await _update_paper_import_status(
                session, run_id, openalex_id, status="added", asset_id=str(asset.id)
            )
            return {"openalex_id": openalex_id, "status": "added", "asset_id": str(asset.id), "reason": None}

        reason = asset.processing_error or f"Processing ended in status '{asset.processing_status.value}'."
        await _update_paper_import_status(session, run_id, openalex_id, status="failed", error=reason)
        return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": reason}


async def _update_paper_import_status(
    session,
    run_id: uuid.UUID,
    openalex_id: str,
    *,
    status: str,
    asset_id: str | None = None,
    error: str | None = None,
) -> None:
    """Mutate one entry of `run.suggested_papers` in place.

    JSONB mutation needs a whole-list reassignment for SQLAlchemy's
    change tracking to see it (mutating a nested dict in place would be
    silently lost) -- so this rebuilds the list rather than editing the
    matched entry's dict directly.
    """
    run = await ResearchRunRepository(session).get_by_id(run_id)
    if run is None or not run.suggested_papers:
        return
    updated = []
    for entry in run.suggested_papers:
        if entry.get("openalex_id") == openalex_id:
            entry = {**entry, "import_status": status}
            if asset_id is not None:
                entry["imported_asset_id"] = asset_id
            if error is not None:
                entry["import_error"] = error
        updated.append(entry)
    run.suggested_papers = updated
    await session.commit()


@celery_app.task(name="workers.finalize_paper_import", bind=True, max_retries=0)
@log_task_execution
def finalize_paper_import(self, results: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Chord callback: once every selected paper has reached a final
    state, start exactly one re-run with the original query -- only if
    at least one paper was added."""
    return _run_task_loop(_finalize_import(results, uuid.UUID(run_id)))


async def _finalize_import(results: list[dict[str, Any]], run_id: uuid.UUID) -> dict[str, Any]:
    added = [result for result in results if result.get("status") == "added"]
    if not added:
        logger.info("paper_import_all_failed", run_id=str(run_id), paper_count=len(results))
        return {"status": "all_failed", "run_id": str(run_id)}

    async with async_session_factory() as session:
        runs = ResearchRunRepository(session)
        run = await runs.get_by_id(run_id)
        if run is None:
            logger.error("paper_import_source_run_missing", run_id=str(run_id))
            return {"status": "source_run_missing", "run_id": str(run_id)}

        new_run = ResearchRun(
            project_id=run.project_id,
            owner_id=run.owner_id,
            task_id=run.task_id,
            parent_run_id=run.id,
            query=run.query,
            status=ResearchRunStatus.PENDING,
            include_assets=True,
            include_web=run.include_web,
            max_results=run.max_results,
        )
        created = await runs.create(new_run)
        await session.commit()
        new_run_id = created.id

    execute_research_run.delay(str(new_run_id))
    logger.info(
        "paper_import_rerun_started",
        source_run_id=str(run_id),
        rerun_id=str(new_run_id),
        added_paper_count=len(added),
    )
    return {"status": "rerun_started", "run_id": str(run_id), "rerun_id": str(new_run_id)}


@celery_app.task(name="workers.recover_execution_job_retry", bind=True)
@log_task_execution
def recover_execution_job_retry(self, job_id: str) -> dict[str, str]:
    """Materialize one interrupted retry claim (Sprint 16 Phase
    7B.21/7B.22). Enqueued per job by `reconciliation.
    reconcile_stale_launching_execution_jobs`, which stays detection
    only -- this task is the one place the guard pipeline and the
    claim-and-materialize transaction actually run.

    Deliberately has no `self.retry(...)`, unlike the pipeline tasks
    above. `recover_interrupted_retry_attempt` is idempotent by
    construction: a lost claim race returns `None` (success, not an
    error), and a guard rejection is terminal for automatic recovery
    (already recorded on `job.reason` by the service) -- retrying would
    re-run the same guards against the same input and reject again. A
    genuine infrastructure failure is left to propagate as a normal
    Celery task failure; the next startup reconciliation pass finds
    this job still stale and re-enqueues it.
    """
    status = _run_task_loop(_recover_retry(uuid.UUID(job_id)))
    return {"status": status, "job_id": job_id}


async def _recover_retry(job_id: uuid.UUID) -> str:
    async with async_session_factory() as session:
        try:
            preparation = await recover_interrupted_retry_attempt(job_id, session)
        except (SecurityBlocked, InputResolutionError):
            return "guard_rejected"
        if preparation is None:
            return "lost_race"
        return "recovered"


#: Persisted on a job's `reason` when this task's guard pipeline rejects
#: it. Fixed and complete -- no interpolation -- matching every other
#: diagnostic constant in this codebase. `prepare_approved_launch`
#: itself deliberately writes nothing on rejection (its own module
#: docstring: "this layer neither catches nor translates" a guard
#: failure, leaving the job at `VALIDATING` for
#: `reconcile_stale_validating_execution_jobs` to eventually diagnose);
#: this constant exists so a synchronous rejection is visible
#: immediately, without waiting for that reconciliation pass.
LAUNCH_GUARD_REJECTED_DIAGNOSTIC = "Execution job's launch was rejected by guard validation."


@celery_app.task(name="workers.launch_execution_job", bind=True)
@log_task_execution
def launch_execution_job(self, job_id: str) -> dict[str, str]:
    """The launch task a `PENDING` job actually runs through (Sprint 16
    Phase 7B.23). Enqueued once, immediately after
    `ExperimentPlanService.request_execution` inserts the job and
    commits it.

    A thin wrapper around the existing, untouched `prepare_approved_
    launch` (`PENDING -> VALIDATING`, guard pipeline, `ExecutionAttempt`
    creation) -- same shape as `recover_execution_job_retry`: no
    `self.retry(...)` anywhere. A guard rejection
    (`SecurityBlocked`/`InputResolutionError`) is diagnosed on
    `job.reason` and treated as terminal, not retried -- re-running the
    same guards against the same job rejects identically every time. A
    genuine infrastructure failure (or `ExecutionJobNotFoundError`/
    `ExecutionJobNotEligibleError`, which should not be reachable given
    this task is only ever enqueued once, right after the job's own
    creation) propagates as a normal Celery task failure.

    Sprint 16 Phase 7B.25: once the guard pipeline approves the launch,
    this now calls `execution_launcher.launcher.execute_approved_launch`
    -- the real Docker create/start/wait/collect/cleanup lifecycle --
    instead of stopping at attempt creation. `launch_execution_job`
    remains the ONLY task that ever reaches this point; no new task,
    queue, or route was added for it (the existing launcher/task
    boundary already covers this).
    """
    status = _run_task_loop(_launch_job(uuid.UUID(job_id)))
    return {"status": status, "job_id": job_id}


async def _launch_job(job_id: uuid.UUID) -> str:
    async with async_session_factory() as session:
        try:
            preparation = await prepare_approved_launch(job_id, session)
        except (SecurityBlocked, InputResolutionError):
            job = await ExecutionJobRepository(session).get_by_id(job_id)
            if job is not None and job.reason != LAUNCH_GUARD_REJECTED_DIAGNOSTIC:
                job.reason = LAUNCH_GUARD_REJECTED_DIAGNOSTIC
                await session.commit()
            return "guard_rejected"

        job = await ExecutionJobRepository(session).get_by_id(job_id)
        outcome = await execute_approved_launch(
            preparation.approved_spec,
            attempt_id=preparation.attempt_id,
            container_name=preparation.container_name,
            project_id=job.project_id,
            owner_id=job.owner_id,
            job_id=job_id,
            session=session,
        )
        return "execution_succeeded" if outcome.succeeded else "execution_failed"


@celery_app.task(name="workers.reconcile_execution_attempt", bind=True)
@log_task_execution
def reconcile_execution_attempt(self, attempt_id: str) -> dict[str, str]:
    """Docker-aware crash recovery for ONE `ExecutionAttempt` (Sprint 16
    Phase 7B.29). Enqueued per attempt by `app.workers.reconciliation.
    reconcile_stale_docker_managed_attempts`, which stays detection only
    -- this task is the one place `execution_launcher.launcher.
    reconcile_attempt`'s real Docker inspect/kill/wait/cleanup I/O
    actually runs, off the fast startup path entirely.

    Deliberately has no `self.retry(...)`, matching `recover_execution_
    job_retry`'s precedent: `reconcile_attempt` is idempotent by
    construction (see its own docstring), so a lost race or a transient
    daemon failure is not an error to retry here -- a daemon-unreachable
    or inspect-failure outcome makes no durable write, which means the
    attempt's `updated_at` is unchanged and the NEXT startup reconciliation
    pass finds it still stale and re-enqueues it naturally.
    """
    outcome = _run_task_loop(_reconcile_attempt(uuid.UUID(attempt_id)))
    return {"action": outcome, "attempt_id": attempt_id}


async def _reconcile_attempt(attempt_id: uuid.UUID) -> str:
    async with async_session_factory() as session:
        outcome = await reconcile_attempt(attempt_id, session)
        return outcome.action
