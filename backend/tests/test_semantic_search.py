"""Sprint 9C: semantic search unit tests.

The ranking tests (TEST 9-12) run against the real Postgres instance
deliberately: pgvector's cosine-distance ordering is a property of the
database query, not something a mock can verify — same rationale
`test_research_citations.py` established for citation propagation.
`KnowledgeBaseService.semantic_search` itself is exercised with a fake
embedding provider so no test starts Ollama.
"""

import uuid
from typing import Any

import pytest

from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType, EmbeddingStatus
from app.modules.assets.models import Asset
from app.modules.assets.ai_profile import AIProfile
from app.modules.auth.models import User
from app.modules.knowledge_base.embeddings import EmbeddingProvider
from app.modules.knowledge_base.enums import EmbeddingProviderName
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.repository import KnowledgeChunkRepository
from app.modules.knowledge_base.service import (
    AssetAccessDeniedError,
    KnowledgeBaseService,
    ProjectAccessDeniedError,
)
from app.modules.projects.models import Project, ProjectStatus, ProjectType

DIM = 1024


def _unit_vector(hot_index: int, dim: int = DIM) -> list[float]:
    """A near-unit vector with one dominant dimension, so cosine
    distance between two of these is easy to reason about by hand."""
    vector = [0.001] * dim
    vector[hot_index] = 1.0
    return vector


class FakeEmbeddingProvider(EmbeddingProvider):
    """Returns a fixed, scripted vector for whatever query text arrives."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.embedded_texts: list[list[str]] = []

    @property
    def name(self) -> EmbeddingProviderName:
        return EmbeddingProviderName.LOCAL

    @property
    def dimensions(self) -> int:
        return DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.append(texts)
        return [self._vector for _ in texts]


async def _make_asset_with_chunks(
    session, project: Project, *, vectors_by_content: dict[str, list[float] | None]
) -> Asset:
    """Create one asset and one completed knowledge chunk per
    `content -> vector` entry (a `None` vector leaves the chunk
    unembedded, to prove those are excluded from results)."""
    space = FakeEmbeddingProvider(_unit_vector(0)).space
    project.active_embedding_space_id = space.id
    asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Search Test Asset",
        asset_type=AssetType.DOCUMENT,
        status=AssetStatus.ACTIVE,
        mime_type="text/plain",
        file_name="doc.txt",
        file_extension=".txt",
        file_size=1,
        storage_path="unused",
        checksum=uuid.uuid4().hex,
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=None,
        processing_status=AssetProcessingStatus.COMPLETED,
    )
    session.add(asset)
    await session.flush()

    for index, (content, vector) in enumerate(vectors_by_content.items()):
        chunk = KnowledgeChunk(
            project_id=project.id,
            asset_id=asset.id,
            chunk_index=index,
            content=content,
            embedding_status=(
                EmbeddingStatus.COMPLETED if vector is not None else EmbeddingStatus.PENDING
            ),
            embedding_provider=EmbeddingProviderName.LOCAL if vector is not None else None,
            embedding=vector,
            embedding_space_id=space.id if vector is not None else None,
            embedding_space=space.as_dict() if vector is not None else None,
        )
        session.add(chunk)
    await session.flush()
    await session.commit()
    return asset


# ---------------------------------------------------------------------------
# TEST 8 — a semantic query generates an embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_search_embeds_the_query_text(session, project):
    provider = FakeEmbeddingProvider(_unit_vector(0))
    await _make_asset_with_chunks(
        session, project, vectors_by_content={"about topic zero": _unit_vector(0)}
    )
    service = KnowledgeBaseService(session, embeddings=provider)

    await service.semantic_search(project.owner_id, query="what is topic zero?", top_k=5)

    assert provider.embedded_texts == [["what is topic zero?"]]


# ---------------------------------------------------------------------------
# TEST 9 — pgvector similarity query returns ordered results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_similarity_search_orders_by_distance(session, project):
    """Three chunks, three different directions; the query vector
    points exactly at one of them -- that one must rank first."""
    await _make_asset_with_chunks(
        session,
        project,
        vectors_by_content={
            "closest to the query": _unit_vector(0),
            "moderately related": _unit_vector(1),
            "unrelated": _unit_vector(500),
        },
    )
    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    ranked = await service.semantic_search(project.owner_id, query="anything", top_k=10)

    assert [chunk.content for chunk, _distance in ranked][0] == "closest to the query"
    distances = [distance for _chunk, distance in ranked]
    assert distances == sorted(distances), "results must be ordered nearest-first"


@pytest.mark.asyncio
async def test_search_excludes_chunks_without_a_completed_embedding(session, project):
    await _make_asset_with_chunks(
        session,
        project,
        vectors_by_content={"embedded": _unit_vector(0), "not yet embedded": None},
    )
    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    ranked = await service.semantic_search(project.owner_id, query="anything", top_k=10)

    assert [chunk.content for chunk, _ in ranked] == ["embedded"]


# ---------------------------------------------------------------------------
# TEST 10 — top_k is respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_k_limits_the_result_count(session, project):
    await _make_asset_with_chunks(
        session,
        project,
        vectors_by_content={f"chunk {i}": _unit_vector(i) for i in range(5)},
    )
    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    ranked = await service.semantic_search(project.owner_id, query="anything", top_k=2)

    assert len(ranked) == 2


# ---------------------------------------------------------------------------
# TEST 11 — asset/project ownership filtering works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_scoped_search_returns_only_that_projects_chunks(session, project):
    other_project = Project(
        owner_id=project.owner_id,
        name="A second project owned by the same user",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(other_project)
    await session.flush()
    await session.commit()

    await _make_asset_with_chunks(
        session, project, vectors_by_content={"in the first project": _unit_vector(0)}
    )
    await _make_asset_with_chunks(
        session, other_project, vectors_by_content={"in the second project": _unit_vector(0)}
    )
    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    ranked = await service.semantic_search(
        project.owner_id, query="anything", top_k=10, project_id=project.id
    )

    assert {chunk.content for chunk, _ in ranked} == {"in the first project"}

    await session.delete(other_project)
    await session.commit()


# ---------------------------------------------------------------------------
# TEST 12 — cross-user asset/project search is rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_searching_another_users_project_is_rejected(session, project):
    stranger = User(
        email=f"pytest-stranger-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Stranger",
        is_active=True,
    )
    session.add(stranger)
    await session.flush()
    await session.commit()

    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    with pytest.raises(ProjectAccessDeniedError):
        await service.semantic_search(
            stranger.id, query="anything", top_k=10, project_id=project.id
        )

    await session.delete(stranger)
    await session.commit()


@pytest.mark.asyncio
async def test_searching_another_users_asset_is_rejected(session, project):
    stranger = User(
        email=f"pytest-stranger-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Stranger",
        is_active=True,
    )
    session.add(stranger)
    await session.flush()
    await session.commit()

    asset = await _make_asset_with_chunks(
        session, project, vectors_by_content={"private": _unit_vector(0)}
    )
    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    with pytest.raises(AssetAccessDeniedError):
        await service.semantic_search(stranger.id, query="anything", top_k=10, asset_id=asset.id)

    await session.delete(stranger)
    await session.commit()


@pytest.mark.asyncio
async def test_cross_project_search_without_project_id_is_scoped_to_owner(session, project):
    """No project_id means "search everything I own", not "search
    everything" -- another owner's chunks must never appear."""
    stranger = User(
        email=f"pytest-stranger-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Stranger",
        is_active=True,
    )
    session.add(stranger)
    await session.flush()

    stranger_project = Project(
        owner_id=stranger.id,
        name="Stranger's project",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(stranger_project)
    await session.flush()
    await session.commit()

    await _make_asset_with_chunks(
        session, project, vectors_by_content={"mine": _unit_vector(0)}
    )
    await _make_asset_with_chunks(
        session, stranger_project, vectors_by_content={"strangers content": _unit_vector(0)}
    )
    provider = FakeEmbeddingProvider(_unit_vector(0))
    service = KnowledgeBaseService(session, embeddings=provider)

    ranked = await service.semantic_search(project.owner_id, query="anything", top_k=10)

    assert {chunk.content for chunk, _ in ranked} == {"mine"}

    await session.delete(stranger)
    await session.commit()
