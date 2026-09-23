"""HTTP routes for browsing the knowledge base, scoped to the
authenticated user.

Chunk listing is read-only: chunks are produced by the asset
processing pipeline, not authored via the API. `POST /search` is the
one write-shaped route in this module, and even it only reads — the
POST verb reflects the request having a body (a query, `top_k`,
optional scope), not a mutation, the same convention
`POST /research/run` and `POST /assets/search` (query-string form)
already use elsewhere in this codebase.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.knowledge_base.dependencies import get_knowledge_base_service
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.schemas import (
    ChunkLocationRead,
    KnowledgeChunkRead,
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticSearchResult,
)
from app.modules.knowledge_base.service import (
    AssetAccessDeniedError,
    KnowledgeBaseService,
    KnowledgeChunkNotFoundError,
    ProjectAccessDeniedError,
)

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge Base"])

_PROJECT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Project not found."
)
_ASSET_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found."
)
_CHUNK_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge chunk not found."
)


@router.post("/search", response_model=SemanticSearchResponse)
async def semantic_search(
    data: SemanticSearchRequest,
    current_user: User = Depends(get_current_user),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> SemanticSearchResponse:
    """Two-stage semantic search over the current user's knowledge chunks.

    Stage 1 embeds `query` with the same local BGE-M3 model used to
    embed chunks and retrieves `candidate_k` chunks by pgvector cosine
    distance. Stage 2 rescores those candidates with the local
    BGE-Reranker-v2-m3 cross-encoder and keeps the best `top_k`. Stage
    3 then drops anything scoring below `relevance_threshold`, so an
    unrelated question returns nothing rather than the least-bad
    chunks — `results` is accepted evidence, not merely ranked
    evidence.

    Optionally scoped to one project and/or one asset the caller owns;
    supplying a project/asset the caller does not own is rejected, not
    silently filtered to an empty result — and ownership is applied in
    stage 1, so the reranker never sees another user's chunks.

    If reranking cannot run, stage 1's results are returned in
    retrieval order and `reranking_status` says so; scores are never
    fabricated and no cloud model is substituted. Synthesis (Sprint 9E)
    does not happen here — this returns ranked evidence, not an answer.
    """
    try:
        outcome = await service.two_stage_search(
            current_user.id,
            query=data.query,
            candidate_k=data.candidate_k,
            top_k=data.top_k,
            project_id=data.project_id,
            asset_id=data.asset_id,
            rerank=data.rerank,
        )
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc
    except AssetAccessDeniedError as exc:
        raise _ASSET_NOT_FOUND from exc

    results = [
        SemanticSearchResult(
            chunk_id=hit.chunk.id,
            asset_id=hit.chunk.asset_id,
            project_id=hit.chunk.project_id,
            content=hit.chunk.content,
            chunk_index=hit.chunk.chunk_index,
            rank=rank,
            retrieval_rank=hit.retrieval_rank,
            distance=hit.retrieval_distance,
            similarity=1.0 - hit.retrieval_distance,
            rerank_score=hit.rerank_score,
        )
        for rank, hit in enumerate(outcome.hits, start=1)
    ]
    return SemanticSearchResponse(
        query=data.query,
        model=settings.embedding_model,
        reranker_model=outcome.reranker_model,
        reranking_status=outcome.reranking_status.value,
        reranking_error=outcome.reranking_error,
        candidate_count=outcome.candidate_count,
        reranked_count=outcome.reranked_count,
        rejected_count=len(outcome.rejected_hits),
        relevance_threshold=outcome.relevance_threshold,
        count=len(results),
        results=results,
    )


@router.get("/chunks", response_model=list[KnowledgeChunkRead])
async def list_chunks(
    project_id: uuid.UUID = Query(...),
    asset_id: uuid.UUID | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> list[KnowledgeChunk]:
    """List knowledge chunks in a project owned by the current user,
    optionally scoped to one asset."""
    try:
        return await service.list_for_owner(
            current_user.id, project_id=project_id, asset_id=asset_id, skip=skip, limit=limit
        )
    except ProjectAccessDeniedError as exc:
        raise _PROJECT_NOT_FOUND from exc


@router.get("/chunks/{chunk_id}", response_model=KnowledgeChunkRead)
async def get_chunk(
    chunk_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> KnowledgeChunk:
    """Fetch a single knowledge chunk owned by the current user."""
    try:
        return await service.get_owned(current_user.id, chunk_id)
    except KnowledgeChunkNotFoundError as exc:
        raise _CHUNK_NOT_FOUND from exc


@router.get("/chunks/{chunk_id}/location", response_model=ChunkLocationRead)
async def get_chunk_location(
    chunk_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> ChunkLocationRead:
    """Where a cited chunk sits in its source PDF, for highlighting it.

    404 when the chunk is missing or not in one of the caller's
    projects. The PDF itself comes from `GET /assets/download/{id}`
    with `document_id`.
    """
    try:
        return await service.get_location(current_user.id, chunk_id)
    except KnowledgeChunkNotFoundError as exc:
        raise _CHUNK_NOT_FOUND from exc
