"""Pydantic v2 response schemas for the knowledge base module.

There are no create/update request schemas for chunks: they are
produced only by the asset processing pipeline, never authored
directly by API clients. `SemanticSearchRequest` is the one write-style
schema in this module — it's a query, not a chunk mutation.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.assets.enums import EmbeddingStatus
from app.modules.knowledge_base.enums import EmbeddingProviderName


class KnowledgeChunkRead(BaseModel):
    """Public representation of a knowledge chunk.

    `embedding` is intentionally omitted — it's a 1024-float vector,
    not useful to typical API consumers browsing the knowledge base,
    and never returned outside `app.modules.knowledge_base.search`
    (which surfaces a similarity score, not the raw vector).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    asset_id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int | None
    #: Sprint 12.1: where this chunk came from within its source asset,
    #: when the extractor recorded a locator — a PDF page number, an
    #: XLSX sheet name, a DOCX/HTML heading or PPTX slide title. `None`
    #: for chunks with no such structure (plain text, CSV).
    page_number: int | None = None
    sheet_name: str | None = None
    section: str | None = None
    embedding_status: EmbeddingStatus
    embedding_provider: EmbeddingProviderName | None
    created_at: datetime
    updated_at: datetime


class SemanticSearchRequest(BaseModel):
    """Request body for `POST /knowledge-base/search`.

    `candidate_k` is stage 1's pool size and `top_k` the final result
    count; `candidate_k` should exceed `top_k` so the stage-2 reranker
    has something to reorder. Both default to `None` here rather than
    to literals, so the configured `RERANKER_CANDIDATE_K`/
    `RERANKER_TOP_K` remain the single source of truth.
    """

    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    project_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    #: Set false to return stage-1 (pgvector) results only. The
    #: response's `reranking_status` reports `disabled` in that case,
    #: so retrieval-only output is never mistaken for reranked output.
    rerank: bool = True


class SemanticSearchResult(BaseModel):
    """One ranked chunk from a semantic search.

    Carries both stage scores. They are different measurements on
    different scales and neither is derived from the other.
    """

    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    asset_id: uuid.UUID
    project_id: uuid.UUID
    content: str
    chunk_index: int
    #: Final position after reranking (1-based). Compare with
    #: `retrieval_rank` to see what stage 2 changed.
    rank: int
    #: Position after stage 1 only (1-based).
    retrieval_rank: int
    #: Stage 1: cosine distance in `[0, 2]` — 0 is identical, 2 is
    #: opposite. Preserved exactly as pgvector returned it, never
    #: overwritten by the reranker.
    distance: float
    #: `1 - distance`: closer to 1 is more similar. A convenience
    #: derived from `distance`, not a second independent measurement.
    similarity: float
    #: Stage 2: the cross-encoder's raw relevance score for this
    #: (query, chunk) pair. `None` when reranking did not run. This is
    #: an unnormalized model output (a logit for BGE-Reranker-v2-m3),
    #: not a probability, and is not comparable to `similarity`.
    rerank_score: float | None = None


class SemanticSearchResponse(BaseModel):
    """Response body for `POST /knowledge-base/search`."""

    query: str
    #: The stage-1 embedding model.
    model: str
    #: The stage-2 reranker model, when reranking was attempted.
    reranker_model: str | None = None
    #: Whether stage 2 actually ran: `completed`, `disabled`,
    #: `unavailable`, or `not_applicable`. Always present, so a caller
    #: can tell reranked results from retrieval-only ones.
    reranking_status: str
    #: Why reranking did not run, when it didn't.
    reranking_error: str | None = None
    #: How many stage-1 candidates were considered before reranking.
    candidate_count: int
    #: How many items the relevance gate judged (accepted + rejected).
    #: Differs from `candidate_count` because reranking returns `top_k`
    #: of the candidate pool, and from `count` because the gate then
    #: removes whatever scored below the threshold.
    reranked_count: int = 0
    #: Evidence that scored below `relevance_threshold` and was
    #: withheld. These chunks are not in `results` and can never be
    #: cited; the count is exposed so an empty result is explainable.
    rejected_count: int = 0
    #: The `rerank_score` an item had to reach to be accepted. `None`
    #: when the gate did not run — which means nothing was filtered,
    #: not that everything passed a check.
    relevance_threshold: float | None = None
    #: Number of accepted results returned, i.e. `len(results)`.
    count: int
    results: list[SemanticSearchResult]


class ChunkLocationRead(BaseModel):
    """Where a cited chunk sits in its source PDF (click-to-source).

    `spans` is `[{page, page_width, page_height, rects: [[x0, y0, x1,
    y1], ...]}]` in PDF points with a top-left origin, so a viewer
    scales by `rendered_width / page_width` with no y-flip. A chunk with
    no stored position (a non-PDF, an OCR'd page, a rotated page, or an
    asset not yet backfilled) comes back as `match_quality="none"` with
    empty `spans` and the chunk's own page, so the viewer can still open
    the right page.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    page_start: int | None
    page_end: int | None
    #: "exact" | "fuzzy" | "none".
    match_quality: str
    spans: list[dict[str, Any]]
