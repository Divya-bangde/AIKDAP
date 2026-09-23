"""`KnowledgeBaseService.get_location` (click-to-source, Phase 3)."""

import uuid

import pytest

from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.knowledge_base.models import ChunkPosition, KnowledgeChunk
from app.modules.knowledge_base.service import KnowledgeBaseService, KnowledgeChunkNotFoundError

SPANS = [{"page": 2, "page_width": 612.0, "page_height": 792.0, "rects": [[72.0, 90.0, 300.0, 102.0]]}]


async def _chunk(session, project, *, page: int) -> KnowledgeChunk:
    asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="paper.pdf",
        asset_type=AssetType.DOCUMENT,
        status=AssetStatus.ACTIVE,
        mime_type="application/pdf",
        file_name="paper.pdf",
        file_extension="pdf",
        file_size=0,
        storage_path="",
        checksum="",
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
    )
    session.add(asset)
    await session.flush()
    chunk = KnowledgeChunk(
        project_id=project.id, asset_id=asset.id, chunk_index=0, content="text", page_number=page
    )
    session.add(chunk)
    await session.flush()
    return chunk


@pytest.mark.asyncio
async def test_location_returns_stored_spans(session, project):
    chunk = await _chunk(session, project, page=2)
    session.add(
        ChunkPosition(
            chunk_id=chunk.id,
            document_id=chunk.asset_id,
            page_start=2,
            page_end=2,
            spans=SPANS,
            match_quality="exact",
        )
    )
    await session.commit()

    location = await KnowledgeBaseService(session).get_location(project.owner_id, chunk.id)

    assert location.document_id == chunk.asset_id
    assert location.match_quality == "exact"
    assert location.spans == SPANS
    assert (location.page_start, location.page_end) == (2, 2)


@pytest.mark.asyncio
async def test_location_without_position_falls_back_to_page(session, project):
    chunk = await _chunk(session, project, page=5)
    await session.commit()

    location = await KnowledgeBaseService(session).get_location(project.owner_id, chunk.id)

    assert location.match_quality == "none"
    assert location.spans == []
    assert location.page_start == 5
    assert location.document_id == chunk.asset_id


@pytest.mark.asyncio
async def test_location_hides_other_users_chunks(session, project):
    chunk = await _chunk(session, project, page=1)
    await session.commit()

    with pytest.raises(KnowledgeChunkNotFoundError):
        await KnowledgeBaseService(session).get_location(uuid.uuid4(), chunk.id)
    with pytest.raises(KnowledgeChunkNotFoundError):
        await KnowledgeBaseService(session).get_location(project.owner_id, uuid.uuid4())
