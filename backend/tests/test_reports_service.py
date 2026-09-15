"""Reports module: ownership, the no-processed-documents guard, and the
report repository's document query (Milestone 10 step 4 -- spec
sections 4 and 5)."""

import uuid

import pytest

from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.reports.repository import ReportRepository


def _make_asset(project, *, asset_type=AssetType.DOCUMENT, processing_status=AssetProcessingStatus.COMPLETED) -> Asset:
    return Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="A processed document",
        description=None,
        asset_type=asset_type,
        status=AssetStatus.ACTIVE,
        mime_type="application/pdf",
        file_name="doc.pdf",
        file_extension="pdf",
        file_size=100,
        storage_path="projects/x/doc.pdf",
        checksum=uuid.uuid4().hex,
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile(summary="A summary.", topics=["topic a"]).model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=processing_status,
    )


@pytest.mark.asyncio
async def test_list_processed_documents_excludes_unprocessed_and_generated_assets(session, project):
    processed = _make_asset(project)
    unprocessed = _make_asset(project, processing_status=AssetProcessingStatus.QUEUED)
    generated = _make_asset(project, asset_type=AssetType.SUMMARY, processing_status=AssetProcessingStatus.COMPLETED)
    session.add_all([processed, unprocessed, generated])
    await session.commit()

    documents = await ReportRepository(session).list_processed_documents(project.id)

    assert [document.id for document in documents] == [processed.id]
