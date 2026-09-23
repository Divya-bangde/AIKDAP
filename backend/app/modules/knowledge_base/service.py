"""Business logic for browsing the knowledge base and (internally)
writing chunks produced by the asset processing pipeline.

Ownership is enforced transitively via the project a chunk belongs to,
matching every other module: "exists but not yours" and "doesn't
exist" both surface as `KnowledgeChunkNotFoundError`.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging.logger import get_logger
from app.modules.assets.enums import EmbeddingStatus
from app.modules.assets.processing.chunker import ProvenancedChunk
from app.modules.assets.repository import AssetRepository
from app.modules.knowledge_base.embeddings import EmbeddingProvider, get_embedding_provider
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.repository import KnowledgeChunkRepository
from app.modules.knowledge_base.schemas import ChunkLocationRead
from app.modules.knowledge_base.relevance import RelevanceGate, get_relevance_gate
from app.modules.knowledge_base.reranking import (
    RerankCandidate,
    RerankerError,
    RerankerProvider,
    RerankingStatus,
    get_reranker_provider,
)
from app.modules.projects.repository import ProjectRepository

logger = get_logger(__name__)


class KnowledgeChunkNotFoundError(Exception):
    """Raised when a chunk does not exist or is not owned by the caller."""


class ProjectAccessDeniedError(Exception):
    """Raised when the caller does not own the project being queried."""


class AssetAccessDeniedError(Exception):
    """Raised when the caller does not own the asset being queried."""


@dataclass(frozen=True)
class RetrievalHit:
    """One result of two-stage retrieval.

    Holds both scores side by side: `retrieval_distance` is stage 1's
    pgvector cosine distance and `rerank_score` is stage 2's
    cross-encoder logit. They measure different things on different
    scales, so neither is ever derived from or overwritten by the
    other; `rerank_score` is `None` when stage 2 did not run.
    """

    chunk: KnowledgeChunk
    retrieval_distance: float
    retrieval_rank: int
    rerank_score: float | None = None


@dataclass(frozen=True)
class SemanticSearchOutcome:
    """The full result of a search, including whether stage 2 ran.

    `hits` is the *accepted* evidence: everything the relevance gate
    admitted, and the only thing a caller may build a context or a
    citation from. Rejected evidence is kept separately so a caller can
    explain an empty result, but it is never part of `hits`.
    """

    hits: list[RetrievalHit]
    reranking_status: RerankingStatus
    candidate_count: int
    reranker_model: str | None = None
    reranking_error: str | None = None
    #: Evidence the relevance gate scored below the threshold. Retained
    #: for diagnostics only — passing it downstream would defeat the
    #: gate.
    rejected_hits: list[RetrievalHit] = field(default_factory=list)
    #: The threshold applied, or `None` when the gate did not run
    #: (nothing was scored, so nothing could be compared).
    relevance_threshold: float | None = None

    @property
    def reranked_count(self) -> int:
        """How many items the gate judged: accepted plus rejected."""
        return len(self.hits) + len(self.rejected_hits)


class KnowledgeBaseService:
    """Coordinates knowledge chunk retrieval, listing, and (re)population."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embeddings: EmbeddingProvider | None = None,
        reranker: RerankerProvider | None = None,
        relevance_gate: RelevanceGate | None = None,
    ) -> None:
        self._session = session
        self._repository = KnowledgeChunkRepository(session)
        self._projects = ProjectRepository(session)
        self._assets = AssetRepository(session)
        self._embeddings = embeddings or get_embedding_provider()
        self._reranker = reranker or get_reranker_provider()
        self._gate = relevance_gate or get_relevance_gate()

    async def _ensure_project_owned(self, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self._projects.get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectAccessDeniedError(project_id)

    async def _ensure_asset_owned(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> None:
        asset = await self._assets.get_by_id(asset_id)
        if asset is None or asset.owner_id != owner_id:
            raise AssetAccessDeniedError(asset_id)

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID,
        asset_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[KnowledgeChunk]:
        """List knowledge chunks in a project owned by the given user."""
        await self._ensure_project_owned(owner_id, project_id)
        return await self._repository.list_by_project(
            project_id, asset_id=asset_id, skip=skip, limit=limit
        )

    async def get_owned(self, owner_id: uuid.UUID, chunk_id: uuid.UUID) -> KnowledgeChunk:
        """Fetch a chunk, ensuring its project belongs to the given user."""
        chunk = await self._repository.get_by_id(chunk_id)
        if chunk is None:
            raise KnowledgeChunkNotFoundError(chunk_id)
        project = await self._projects.get_by_id(chunk.project_id)
        if project is None or project.owner_id != owner_id:
            raise KnowledgeChunkNotFoundError(chunk_id)
        return chunk

    async def get_location(self, owner_id: uuid.UUID, chunk_id: uuid.UUID) -> ChunkLocationRead:
        """Where an owned chunk sits in its source PDF (click-to-source).

        A chunk with no stored position degrades to its own page with
        `match_quality="none"` rather than a 404: the chunk exists and
        is the caller's, only the highlight is unavailable.
        """
        chunk = await self.get_owned(owner_id, chunk_id)
        position = await self._repository.get_position(chunk_id)
        if position is None:
            return ChunkLocationRead(
                chunk_id=chunk.id,
                document_id=chunk.asset_id,
                page_start=chunk.page_number,
                page_end=chunk.page_number,
                match_quality="none",
                spans=[],
            )
        return ChunkLocationRead(
            chunk_id=chunk.id,
            document_id=position.document_id,
            page_start=position.page_start,
            page_end=position.page_end,
            match_quality=position.match_quality,
            spans=position.spans,
        )

    async def replace_chunks_for_asset(
        self,
        *,
        project_id: uuid.UUID,
        asset_id: uuid.UUID,
        chunk_texts: list[str] | None = None,
        chunks: list[ProvenancedChunk] | None = None,
    ) -> list[KnowledgeChunk]:
        """Replace all chunks for an asset with a freshly extracted set.

        Internal API used by the processing pipeline (not exposed via
        the public router). Deletes any chunks from a prior run first,
        so reprocessing an asset is idempotent rather than accumulating
        duplicates.

        Accepts either bare `chunk_texts` (no provenance — used by
        callers/tests that only care about content) or `chunks`
        (Sprint 12.1's `ProvenancedChunk`, carrying page/sheet/section).
        Exactly one must be supplied.
        """
        if (chunk_texts is None) == (chunks is None):
            raise ValueError("Pass exactly one of chunk_texts or chunks.")

        await self._repository.delete_by_asset(asset_id)

        if chunk_texts is not None:
            rows = [
                KnowledgeChunk(
                    project_id=project_id,
                    asset_id=asset_id,
                    chunk_index=index,
                    content=text,
                    embedding_status=EmbeddingStatus.PENDING,
                )
                for index, text in enumerate(chunk_texts)
            ]
        else:
            rows = [
                KnowledgeChunk(
                    project_id=project_id,
                    asset_id=asset_id,
                    chunk_index=index,
                    content=chunk.text,
                    page_number=chunk.page_number,
                    sheet_name=chunk.sheet_name,
                    section=chunk.section,
                    embedding_status=EmbeddingStatus.PENDING,
                )
                for index, chunk in enumerate(chunks or [])
            ]

        created = await self._repository.bulk_create(rows)
        await self._session.commit()
        return created

    async def semantic_search(
        self,
        owner_id: uuid.UUID,
        *,
        query: str,
        top_k: int = 10,
        project_id: uuid.UUID | None = None,
        asset_id: uuid.UUID | None = None,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Rank the caller's own knowledge chunks by semantic similarity
        to `query`.

        Ownership is validated explicitly here (not only via the
        repository's `Project.owner_id` join, which would otherwise
        just return an empty result for someone else's project/asset
        rather than a clear error) whenever `project_id`/`asset_id` are
        supplied, matching every other module's "exists but not yours"
        -> a typed access-denied error convention.
        """
        if project_id is not None:
            await self._ensure_project_owned(owner_id, project_id)
        if asset_id is not None:
            await self._ensure_asset_owned(owner_id, asset_id)

        embedded = await self._embeddings.embed([query])
        query_vector = embedded[0]

        return await self._repository.search_similar(
            owner_id,
            query_vector=query_vector,
            project_id=project_id,
            asset_id=asset_id,
            top_k=top_k,
        )

    async def two_stage_search(
        self,
        owner_id: uuid.UUID,
        *,
        query: str,
        candidate_k: int | None = None,
        top_k: int | None = None,
        project_id: uuid.UUID | None = None,
        asset_id: uuid.UUID | None = None,
        rerank: bool = True,
    ) -> SemanticSearchOutcome:
        """Retrieve candidates with BGE-M3 + pgvector, then rerank them.

        Stage 1 over-retrieves `candidate_k` (>= `top_k`) so stage 2 has
        a pool to reorder; retrieving only `top_k` first would make
        reranking a no-op. Ownership is enforced in stage 1 — the
        reranker only ever sees chunks the caller already owns, so
        there is no path where unrestricted candidates are filtered
        after scoring.

        Never raises for a reranker failure: stage 1's results are
        returned in retrieval order with a `reranking_status` that says
        stage 2 did not run. The status is always explicit, so a caller
        can never mistake retrieval-only output for reranked output.
        """
        effective_top_k = top_k or settings.reranker_top_k
        effective_candidate_k = candidate_k or settings.reranker_candidate_k
        # A candidate pool smaller than the requested result count is a
        # caller mistake, not a reason to fail: widen it so stage 1 can
        # still supply `top_k` results.
        effective_candidate_k = max(effective_candidate_k, effective_top_k)

        candidates = await self.semantic_search(
            owner_id,
            query=query,
            top_k=effective_candidate_k,
            project_id=project_id,
            asset_id=asset_id,
        )
        hits = [
            RetrievalHit(chunk=chunk, retrieval_distance=distance, retrieval_rank=rank)
            for rank, (chunk, distance) in enumerate(candidates, start=1)
        ]

        if not hits:
            return SemanticSearchOutcome(
                hits=[],
                reranking_status=RerankingStatus.NOT_APPLICABLE,
                candidate_count=0,
            )

        if not rerank or not settings.reranker_enabled:
            return SemanticSearchOutcome(
                hits=hits[:effective_top_k],
                reranking_status=RerankingStatus.DISABLED,
                candidate_count=len(hits),
            )

        return await self._rerank_hits(
            query=query, hits=hits, top_k=effective_top_k
        )

    async def _rerank_hits(
        self, *, query: str, hits: list[RetrievalHit], top_k: int
    ) -> SemanticSearchOutcome:
        """Run stage 2, degrading to retrieval order if it cannot run."""
        candidates = [
            RerankCandidate(
                chunk_id=hit.chunk.id,
                asset_id=hit.chunk.asset_id,
                project_id=hit.chunk.project_id,
                chunk_index=hit.chunk.chunk_index,
                content=hit.chunk.content,
                retrieval_distance=hit.retrieval_distance,
            )
            for hit in hits
        ]
        by_chunk_id = {hit.chunk.id: hit for hit in hits}

        try:
            reranked = await self._reranker.rerank(
                query=query, candidates=candidates, top_k=top_k
            )
        except RerankerError as exc:
            logger.warning(
                "reranking_unavailable",
                model=self._reranker.model,
                candidate_count=len(candidates),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return SemanticSearchOutcome(
                hits=hits[:top_k],
                reranking_status=RerankingStatus.UNAVAILABLE,
                candidate_count=len(hits),
                reranker_model=self._reranker.model,
                reranking_error=str(exc),
            )

        ordered = [
            RetrievalHit(
                chunk=by_chunk_id[item.candidate.chunk_id].chunk,
                # Stage 1's distance and rank are carried through
                # untouched, so a caller can see how reranking changed
                # the ordering.
                retrieval_distance=item.candidate.retrieval_distance,
                retrieval_rank=by_chunk_id[item.candidate.chunk_id].retrieval_rank,
                rerank_score=item.rerank_score,
            )
            for item in reranked
        ]
        logger.info(
            "reranking_completed",
            model=self._reranker.model,
            candidate_count=len(candidates),
            returned=len(ordered),
        )

        # Stage 3: relevance. Reranking answered "in what order"; this
        # answers "at all". Applied here, in the retrieval layer, so
        # every consumer — the search API and the research graph alike
        # — gets already-filtered evidence and none of them has to
        # remember to filter it themselves.
        decision = self._gate.apply(
            ordered, scores=[hit.rerank_score for hit in ordered], query=query
        )
        return SemanticSearchOutcome(
            hits=decision.accepted,
            reranking_status=RerankingStatus.COMPLETED,
            candidate_count=len(candidates),
            reranker_model=self._reranker.model,
            rejected_hits=decision.rejected,
            relevance_threshold=decision.threshold if decision.applied else None,
        )
