"""Data-access layer for the `KnowledgeChunk` model.

Contains only persistence operations; transaction boundaries (commit)
and ownership rules live in `service.py`. The one exception is
`search_similar`: it takes `owner_id` and joins against `Project`
directly, enforcing ownership at the SQL level rather than trusting a
separate pre-check the caller could forget — a stricter guarantee than
`list_by_project`'s pattern (an explicit `project_id`, previously
validated by the caller), appropriate for a new query that can
optionally span every project a user owns.
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.enums import EmbeddingStatus
from app.modules.knowledge_base.models import ChunkPosition, KnowledgeChunk
from app.modules.projects.models import Project


class KnowledgeChunkRepository:
    """Encapsulates all direct database access for `KnowledgeChunk` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, chunk_id: uuid.UUID) -> KnowledgeChunk | None:
        """Fetch a chunk by primary key, or None if not found."""
        return await self._session.get(KnowledgeChunk, chunk_id)

    async def get_position(self, chunk_id: uuid.UUID) -> ChunkPosition | None:
        """Fetch a chunk's stored page position, or None if it has none."""
        result = await self._session.execute(
            select(ChunkPosition).where(ChunkPosition.chunk_id == chunk_id)
        )
        return result.scalar_one_or_none()

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        *,
        asset_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[KnowledgeChunk]:
        """List chunks in a project, optionally scoped to one asset, in
        stable reading order (by asset, then chunk position)."""
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.project_id == project_id)
        if asset_id is not None:
            stmt = stmt.where(KnowledgeChunk.asset_id == asset_id)
        stmt = (
            stmt.order_by(KnowledgeChunk.asset_id, KnowledgeChunk.chunk_index)
            .offset(skip)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_asset(self, asset_id: uuid.UUID) -> None:
        """Delete all chunks belonging to an asset (used before reprocessing,
        so reprocessing is idempotent rather than appending duplicates)."""
        await self._session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.asset_id == asset_id)
        )

    async def bulk_create(self, chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Insert multiple chunk rows and flush to populate generated fields."""
        self._session.add_all(chunks)
        await self._session.flush()
        for chunk in chunks:
            await self._session.refresh(chunk)
        return chunks

    async def search_similar(
        self,
        owner_id: uuid.UUID,
        *,
        query_vector: list[float],
        project_id: uuid.UUID | None = None,
        asset_id: uuid.UUID | None = None,
        top_k: int = 10,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Rank chunks by cosine distance to `query_vector`, scoped to
        projects the given user owns.

        Returns `(chunk, distance)` pairs in ascending distance (most
        similar first) — pgvector's `<=>` cosine-distance operator,
        via `Vector.cosine_distance`, exposed directly rather than
        computed in Python so the database does the ranking and
        `top_k` limiting itself. Only chunks with a completed
        embedding are eligible: a `PENDING`/`FAILED` row has no vector
        to compare against, and a stale one from a previous provider
        would silently corrupt ranking.
        """
        distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(KnowledgeChunk, distance.label("distance"))
            .join(Project, Project.id == KnowledgeChunk.project_id)
            .where(Project.owner_id == owner_id)
            .where(KnowledgeChunk.embedding.is_not(None))
            .where(KnowledgeChunk.embedding_status == EmbeddingStatus.COMPLETED)
        )
        if project_id is not None:
            stmt = stmt.where(KnowledgeChunk.project_id == project_id)
        if asset_id is not None:
            stmt = stmt.where(KnowledgeChunk.asset_id == asset_id)
        stmt = stmt.order_by(distance.asc()).limit(top_k)

        result = await self._session.execute(stmt)
        return [(row[0], float(row[1])) for row in result.all()]
