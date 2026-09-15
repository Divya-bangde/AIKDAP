"""Direct, database-backed tests for the report graph's two real
helpers (hardening item 4): `RepositoryDocumentLister` and
`KnowledgeBaseSectionSearcher`. Real Postgres + pgvector; a fixed-vector
fake embedding provider stands in for the model, and reranking is
switched off, so no model is ever called."""

import uuid

import pytest

from app.agents.reports.nodes import KnowledgeBaseSectionSearcher, RepositoryDocumentLister
from app.core.config import settings
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import (
    AssetProcessingStatus,
    AssetSource,
    AssetStatus,
    AssetType,
    EmbeddingStatus,
)
from app.modules.assets.models import Asset
from app.modules.knowledge_base.embeddings import EmbeddingProvider
from app.modules.knowledge_base.enums import EmbeddingProviderName
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.service import KnowledgeBaseService
from app.modules.projects.models import Project, ProjectStatus, ProjectType

DIM = 1024


def _vector() -> list[float]:
    vector = [0.001] * DIM
    vector[0] = 1.0
    return vector


class _FixedEmbeddings(EmbeddingProvider):
    @property
    def name(self) -> EmbeddingProviderName:
        return EmbeddingProviderName.LOCAL

    @property
    def dimensions(self) -> int:
        return DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector() for _ in texts]


def _document(project: Project, *, title: str, status=AssetProcessingStatus.COMPLETED, asset_type=AssetType.DOCUMENT, source=AssetSource.UPLOAD) -> Asset:
    return Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title=title,
        description=None,
        asset_type=asset_type,
        status=AssetStatus.ACTIVE,
        mime_type="application/pdf",
        file_name=f"{title.lower().replace(' ', '-')}.pdf",
        file_extension="pdf",
        file_size=10,
        storage_path="projects/x/doc.pdf",
        checksum=uuid.uuid4().hex,
        source=source,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile(summary=f"About {title}.", topics=["poultry"]).model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=status,
    )


async def _other_project(session, project: Project) -> Project:
    other = Project(
        owner_id=project.owner_id,
        name="Another project",
        description=None,
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(other)
    await session.commit()
    return other


@pytest.mark.asyncio
async def test_document_lister_returns_only_this_projects_processed_documents(session, project):
    other = await _other_project(session, project)
    processed = _document(project, title="Processed Paper")
    session.add_all(
        [
            processed,
            _document(project, title="Still Queued", status=AssetProcessingStatus.QUEUED),
            _document(project, title="A Summary", asset_type=AssetType.SUMMARY, source=AssetSource.GENERATED),
            _document(other, title="Other Project Paper"),
        ]
    )
    await session.commit()

    lister = RepositoryDocumentLister(session)
    for project_id in (str(project.id), project.id):
        documents = await lister.list_processed(project_id)

        assert [document["asset_id"] for document in documents] == [str(processed.id)]
        assert documents[0]["title"] == "Processed Paper"
        assert documents[0]["summary"] == "About Processed Paper."


@pytest.mark.asyncio
async def test_section_searcher_returns_only_this_projects_evidence_for_str_and_uuid_ids(session, project, monkeypatch):
    monkeypatch.setattr(settings, "reranker_enabled", False)
    other = await _other_project(session, project)
    mine = _document(project, title="My Paper")
    theirs = _document(other, title="Their Paper")
    session.add_all([mine, theirs])
    await session.flush()
    session.add_all(
        [
            KnowledgeChunk(
                project_id=project.id,
                asset_id=mine.id,
                chunk_index=0,
                content="my poultry evidence",
                embedding_status=EmbeddingStatus.COMPLETED,
                embedding_provider=EmbeddingProviderName.LOCAL,
                embedding=_vector(),
            ),
            KnowledgeChunk(
                project_id=other.id,
                asset_id=theirs.id,
                chunk_index=0,
                content="other project's poultry evidence",
                embedding_status=EmbeddingStatus.COMPLETED,
                embedding_provider=EmbeddingProviderName.LOCAL,
                embedding=_vector(),
            ),
        ]
    )
    await session.commit()

    searcher = KnowledgeBaseSectionSearcher(
        session, knowledge_base=KnowledgeBaseService(session, embeddings=_FixedEmbeddings())
    )
    for owner_id, project_id in ((str(project.owner_id), str(project.id)), (project.owner_id, project.id)):
        evidence = await searcher.search(owner_id=owner_id, project_id=project_id, query="poultry", limit=5)

        assert [item["asset_id"] for item in evidence] == [str(mine.id)]
        assert evidence[0]["snippet"] == "my poultry evidence"
