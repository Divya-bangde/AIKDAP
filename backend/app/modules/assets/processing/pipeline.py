"""Asset processing pipeline orchestration.

Runs extract -> chunk -> embed -> (enqueue) AI understanding for one
asset. This is the only place that sequences those steps; `app.workers.
tasks.process_uploaded_asset` is a thin Celery bridge around
`AssetProcessingService.process_asset`, and `app.modules.assets.
router`'s manual reprocess endpoint enqueues the same Celery task — so
there is exactly one pipeline implementation, run either automatically
after upload or on demand.

AI document understanding (Sprint 9B, local Qwen through Ollama) and
embedding generation (Sprint 9C, local BGE-M3 through Ollama) are both
best-effort and strictly non-fatal to the deterministic pipeline:
neither failure changes `processing_status`/`processing_error`, and
neither discards the extracted text/chunks already persisted. See
`AIProfileStatus` (Qwen) and `EmbeddingStatus` (BGE-M3) for why each is
tracked on its own field rather than through the asset's own status.

Sprint 16 Phase 8.11 Part D: embedding runs inline (fast, and the one
thing search/retrieval actually needs); AI understanding is enqueued as
its own Celery task (`workers.generate_ai_metadata`, which already
existed) rather than awaited here, since a real measurement against
this deployment's local Ollama server showed it serializes requests --
18 sequential ~6-17s calls on a typical paper would otherwise leave a
document unsearchable for minutes after upload for no retrieval
benefit.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import LLMError
from app.core.logging.logger import get_logger
from app.modules.assets.ai_profile import AIProfile, AIProfileStatus
from app.modules.assets.enums import AssetProcessingStatus, EmbeddingStatus
from app.modules.assets.models import Asset
from app.modules.assets.processing.chunker import chunk_document
from app.modules.assets.processing.positions import store_chunk_positions
from app.modules.assets.processing.document_understanding import (
    DocumentUnderstandingError,
    QwenDocumentUnderstandingService,
    get_document_understanding_service,
)
from app.modules.assets.processing.extractors import (
    ExtractionFailedError,
    ExtractionNotSupportedError,
    OcrRequiredError,
    get_text_extractor,
)
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import StorageProvider
from app.modules.knowledge_base.embeddings import EmbeddingProvider, get_embedding_provider
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.service import KnowledgeBaseService

logger = get_logger(__name__)


class AssetProcessingService:
    """Runs the extract -> chunk -> AI understanding -> (future) embed
    pipeline for one asset."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageProvider,
        *,
        chunk_size: int,
        chunk_overlap: int,
        understanding: QwenDocumentUnderstandingService | None = None,
        embeddings: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._storage = storage
        self._assets = AssetRepository(session)
        self._knowledge_base = KnowledgeBaseService(session)
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._understanding = understanding or get_document_understanding_service()
        self._embeddings = embeddings or get_embedding_provider()

    async def process_asset(self, asset_id: uuid.UUID) -> None:
        """Extract, chunk, and store knowledge chunks for one asset.

        Idempotent: rerunning replaces any chunks from a previous run.
        Never raises — failures are recorded on the asset's
        `processing_status`/`processing_error` rather than propagated,
        since this is the primary failure-reporting mechanism for a
        background job (visible via `GET /assets/{id}`, not just
        buried in a task queue's result backend).
        """
        asset = await self._assets.get_by_id(asset_id)
        if asset is None:
            logger.warning("asset_processing_asset_missing", asset_id=str(asset_id))
            return

        asset.processing_status = AssetProcessingStatus.EXTRACTING
        asset.processing_started_at = datetime.now(timezone.utc)
        asset.processing_error = None
        await self._session.commit()

        try:
            content = await self._storage.read(asset.storage_path)
            extractor = get_text_extractor(asset.mime_type)
            extracted = await extractor.extract(content)

            if extracted.warnings:
                logger.info(
                    "asset_extraction_warnings",
                    asset_id=str(asset_id),
                    warnings=extracted.warnings,
                )

            asset.processing_status = AssetProcessingStatus.CHUNKING
            await self._session.commit()

            provenanced_chunks = chunk_document(
                extracted, chunk_size=self._chunk_size, chunk_overlap=self._chunk_overlap
            )
            if not provenanced_chunks:
                # A supported format that parsed successfully but
                # yielded no usable content (a genuinely blank DOCX, an
                # empty spreadsheet). Never reported as COMPLETED with
                # zero chunks -- FAILED, with a reason a user can
                # actually act on.
                raise ExtractionFailedError(
                    "No extractable content found in this file "
                    "(the document parsed successfully but appears to be empty)."
                )
            chunks = await self._knowledge_base.replace_chunks_for_asset(
                project_id=asset.project_id, asset_id=asset.id, chunks=provenanced_chunks
            )

            await self._store_chunk_positions(asset, content, chunks)

            asset.processing_status = AssetProcessingStatus.COMPLETED
            asset.processing_completed_at = datetime.now(timezone.utc)
            await self._session.commit()
            logger.info(
                "asset_processing_completed",
                asset_id=str(asset_id),
                chunk_count=len(provenanced_chunks),
            )

            # Best-effort from here: the deterministic pipeline already
            # succeeded and `processing_status` is final. Neither the
            # Qwen nor the embedding step below can change it — only
            # `ai_profile` / each chunk's own `embedding_status`.
            #
            # Sprint 16 Phase 8.11 Part D: embedding runs first and
            # inline, AI understanding is enqueued as its own task,
            # not awaited here. Measured against the real local Ollama
            # server (4 concurrent `qwen3.5:4b` calls via
            # `asyncio.gather` each took *longer* than the last --
            # 2.2s/4.2s/6.1s/8.1s -- confirming it serializes requests
            # rather than parallelizing them, so `asyncio.gather`-ing
            # the existing sequential section loop would have bought
            # nothing. A 13-page paper's 18 sequential Qwen calls
            # (6-17s each) were previously the reason a document wasn't
            # searchable for minutes after upload, even though
            # embedding itself (the one thing retrieval needs) takes
            # seconds. Understanding is already best-effort with its
            # own `ai_profile.status` field, so running it after,
            # rather than before, embedding changes nothing about what
            # a caller can observe once it completes -- only when.
            await self._run_embedding(asset, chunks)
            self._enqueue_ai_understanding(asset.id)
            if asset.mime_type == "application/pdf":
                self._enqueue_paper_lookup(asset.id)

        except ExtractionNotSupportedError as exc:
            asset.processing_status = AssetProcessingStatus.UNSUPPORTED
            asset.processing_error = str(exc)
            asset.processing_completed_at = datetime.now(timezone.utc)
            await self._session.commit()
            logger.info("asset_processing_unsupported", asset_id=str(asset_id), reason=str(exc))

        except OcrRequiredError as exc:
            # Sprint 12.1: the file IS a supported format and DID parse
            # structurally -- it's a scanned document with zero
            # machine-readable text. Distinct from both UNSUPPORTED (no
            # extractor exists at all) and FAILED (the bytes didn't
            # parse), so the frontend can say something specific
            # ("this needs OCR") instead of a generic failure.
            asset.processing_status = AssetProcessingStatus.OCR_REQUIRED
            asset.processing_error = str(exc)
            asset.processing_completed_at = datetime.now(timezone.utc)
            await self._session.commit()
            logger.info("asset_processing_ocr_required", asset_id=str(asset_id), reason=str(exc))

        except ExtractionFailedError as exc:
            # A supported format whose bytes didn't actually parse:
            # corrupted, truncated, or password-protected. Recorded as
            # FAILED (not UNSUPPORTED) — this format works in general,
            # this specific file is broken.
            asset.processing_status = AssetProcessingStatus.FAILED
            asset.processing_error = str(exc)
            asset.processing_completed_at = datetime.now(timezone.utc)
            await self._session.commit()
            logger.info("asset_processing_extraction_failed", asset_id=str(asset_id), reason=str(exc))

        except Exception as exc:  # noqa: BLE001 - recorded on the asset, not swallowed
            asset.processing_status = AssetProcessingStatus.FAILED
            asset.processing_error = str(exc)
            asset.processing_completed_at = datetime.now(timezone.utc)
            await self._session.commit()
            logger.error("asset_processing_failed", asset_id=str(asset_id), error=str(exc))

    async def _run_ai_understanding(self, asset: Asset, text: str) -> None:
        """Best-effort local AI document understanding via Qwen/Ollama.

        Never raises and never falls back to a cloud model: a failure
        here is reported on `ai_profile.status`/`ai_profile.error`, not
        masked by silently calling Gemini (Sprint 9A's cloud gateway).
        The two failure branches below distinguish, per Sprint 9B's
        requirements, "Qwen answered but the answer was unusable"
        (`DocumentUnderstandingError` — empty text, invalid JSON,
        schema mismatch) from "Qwen/Ollama could not be reached at
        all" (`LLMError` — service down, model not installed, timeout).
        """
        profile = AIProfile.model_validate(asset.ai_profile or {})
        try:
            metadata = await self._understanding.analyze(text)
        except DocumentUnderstandingError as exc:
            profile.status = AIProfileStatus.FAILED
            profile.error = str(exc)
            logger.warning(
                "asset_ai_understanding_failed", asset_id=str(asset.id), reason=str(exc)
            )
        except LLMError as exc:
            profile.status = AIProfileStatus.UNAVAILABLE
            profile.error = str(exc)
            logger.warning(
                "asset_ai_understanding_unavailable",
                asset_id=str(asset.id),
                error_type=type(exc).__name__,
                reason=str(exc),
            )
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
            logger.info(
                "asset_ai_understanding_completed",
                asset_id=str(asset.id),
                generated_by=settings.qwen_model,
                keyword_count=len(metadata.keywords),
                entity_count=len(metadata.entities),
                truncated=metadata.truncated,
                processed_sections=metadata.processed_sections,
                total_sections=metadata.total_sections,
            )

        asset.ai_profile = profile.model_dump(mode="json")
        await self._session.commit()

    async def _store_chunk_positions(
        self, asset: Asset, content: bytes, chunks: list[KnowledgeChunk]
    ) -> None:
        """Record where each PDF chunk sits on its page. Never fatal:
        a locating failure is stored as `match_quality="none"` inside
        `store_chunk_positions`, and anything else is only logged, so
        ingestion never fails because of highlighting."""
        if asset.mime_type != "application/pdf" or not chunks:
            return
        try:
            await store_chunk_positions(asset.id, content, chunks)
        except Exception as exc:  # noqa: BLE001 - highlighting is best-effort
            logger.warning(
                "chunk_positions_failed",
                asset_id=str(asset.id),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def _enqueue_ai_understanding(self, asset_id: uuid.UUID) -> None:
        """Fire-and-forget the existing standalone `generate_ai_metadata`
        Celery task (Sprint 16 Phase 8.11 Part D).

        That task already existed, already ran the same
        `QwenDocumentUnderstandingService` this class's own
        `_run_ai_understanding` calls, and its docstring already said
        "standalone step for future composition" -- this is that
        composition, not a new implementation. It re-extracts the
        asset's text itself rather than reusing `extracted.full_text`
        from this call, so it has no dependency on this method's stack
        frame and can be retried independently by Celery like any
        other task.

        Imported locally, not at module level: `app.workers.tasks`
        imports `get_asset_processing_service` from this module for
        `process_uploaded_asset`, so a top-level import here would be
        circular (the same reason `research.service` imports
        `execute_research_run` locally).
        """
        from app.workers.tasks import generate_ai_metadata

        generate_ai_metadata.delay(str(asset_id))

    def _enqueue_paper_lookup(self, asset_id: uuid.UUID) -> None:
        """Fire-and-forget the OpenAlex match for a PDF (paper map).

        Best-effort like highlighting: a broker hiccup here is logged,
        never turned into a processing failure -- the backfill finds any
        PDF without a match later. Imported locally for the same
        circular-import reason as `_enqueue_ai_understanding`.
        """
        from app.workers.tasks import lookup_paper_reference

        try:
            lookup_paper_reference.delay(str(asset_id))
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning(
                "paper_lookup_enqueue_failed", asset_id=str(asset_id), error_type=type(exc).__name__
            )

    async def _run_embedding(self, asset: Asset, chunks: list[KnowledgeChunk]) -> None:
        """Best-effort embedding generation for one asset's chunks.

        One batch call for all of an asset's chunks (`embed_documents`
        semantics, not one round trip per chunk) — matches the
        `EmbeddingProvider.embed(texts: list[str])` interface Sprint 6
        already defined. Never raises and never fabricates a vector:
        a failure marks every chunk `FAILED` with a recorded reason,
        leaving `content` (already committed above) untouched.

        Sprint 9I: also rolls the outcome up onto
        `asset.ai_profile.embedding_status`. Per-chunk status on
        `KnowledgeChunk` has always been the retrieval-time source of
        truth and this fix does not change that — but the asset-level
        field existed in the API response and was never written by any
        code path (a leftover default from before embedding was wired
        in), so `GET /assets/{id}` reported `pending` forever even
        after every chunk genuinely completed. Read fresh from
        `asset.ai_profile` rather than trusting a stale local variable,
        since `_run_ai_understanding` above may have just written to it.
        """
        if not chunks:
            return

        for chunk in chunks:
            chunk.embedding_status = EmbeddingStatus.PROCESSING
        await self._session.commit()

        try:
            vectors = await self._embeddings.embed([chunk.content for chunk in chunks])
        except Exception as exc:  # noqa: BLE001 - recorded per chunk, not swallowed
            for chunk in chunks:
                chunk.embedding_status = EmbeddingStatus.FAILED
            profile = AIProfile.model_validate(asset.ai_profile or {})
            profile.embedding_status = EmbeddingStatus.FAILED
            asset.ai_profile = profile.model_dump(mode="json")
            await self._session.commit()
            logger.warning(
                "asset_embedding_failed",
                asset_id=str(chunks[0].asset_id),
                chunk_count=len(chunks),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return

        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_status = EmbeddingStatus.COMPLETED
            chunk.embedding_provider = self._embeddings.name
        profile = AIProfile.model_validate(asset.ai_profile or {})
        profile.embedding_status = EmbeddingStatus.COMPLETED
        asset.ai_profile = profile.model_dump(mode="json")
        await self._session.commit()
        logger.info(
            "asset_embedding_completed",
            asset_id=str(chunks[0].asset_id),
            chunk_count=len(chunks),
            provider=self._embeddings.name.value,
            dimension=self._embeddings.dimensions,
        )


def get_asset_processing_service(
    session: AsyncSession, storage: StorageProvider
) -> AssetProcessingService:
    """Factory for `AssetProcessingService`, used by the Celery bridge
    in `app.workers.tasks` (not a FastAPI route dependency, since
    Celery tasks run outside the request/response cycle)."""
    return AssetProcessingService(
        session,
        storage,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
