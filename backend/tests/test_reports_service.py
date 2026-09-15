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


from app.modules.reports.schemas import ReportKind
from app.modules.reports.service import (
    NoProcessedDocumentsError,
    ProjectAccessDeniedError,
    ReportNotFoundError,
    ReportNotReadyError,
    ReportService,
)


@pytest.mark.asyncio
async def test_generate_synopsis_requires_a_processed_document(session, project, monkeypatch):
    monkeypatch.setattr("app.modules.reports.service.generate_report", type("_T", (), {"delay": lambda *a, **k: None}))

    with pytest.raises(NoProcessedDocumentsError):
        await ReportService(session).generate_synopsis(project.owner_id, project.id, ReportKind.STUDY_SUMMARY)


@pytest.mark.asyncio
async def test_generate_synopsis_rejects_a_project_the_caller_does_not_own(session, project):
    with pytest.raises(ProjectAccessDeniedError):
        await ReportService(session).generate_synopsis(uuid.uuid4(), project.id, ReportKind.STUDY_SUMMARY)


@pytest.mark.asyncio
async def test_generate_synopsis_creates_a_pending_generated_asset_and_dispatches_celery(session, project, monkeypatch):
    dispatched: list[str] = []
    monkeypatch.setattr(
        "app.modules.reports.service.generate_report",
        type("_T", (), {"delay": staticmethod(lambda asset_id: dispatched.append(asset_id))}),
    )
    session.add(_make_asset(project))
    await session.commit()

    asset = await ReportService(session).generate_synopsis(project.owner_id, project.id, ReportKind.PROJECT_SYNOPSIS)

    assert asset.source.value == "generated"
    assert asset.asset_type.value == "report"
    assert asset.processing_status.value == "pending"
    assert dispatched == [str(asset.id)]


@pytest.mark.asyncio
async def test_get_owned_report_rejects_another_owner(session, project):
    session.add(_make_asset(project))
    await session.commit()
    monkeypatch_target = None  # no Celery dispatch needed for a direct asset creation below

    from app.modules.assets.ai_profile import AIProfile
    from app.modules.assets.enums import AssetSource, AssetStatus, AssetType
    from app.modules.assets.models import Asset

    report_asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Study Summary",
        description=None,
        asset_type=AssetType.SUMMARY,
        status=AssetStatus.ACTIVE,
        mime_type="application/json",
        file_name="study-summary.json",
        file_extension="json",
        file_size=0,
        storage_path="",
        checksum="",
        source=AssetSource.GENERATED,
        version=1,
        tags=[],
        asset_metadata={"kind": "study_summary", "sections": []},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.PENDING,
    )
    session.add(report_asset)
    await session.commit()

    with pytest.raises(ReportNotFoundError):
        await ReportService(session).get_owned_report(uuid.uuid4(), report_asset.id)

    fetched = await ReportService(session).get_owned_report(project.owner_id, report_asset.id)
    assert fetched.id == report_asset.id


@pytest.mark.asyncio
async def test_render_download_rejects_a_report_still_generating(session, project):
    from app.modules.assets.ai_profile import AIProfile
    from app.modules.assets.enums import AssetSource, AssetStatus, AssetType
    from app.modules.assets.models import Asset

    report_asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Study Summary",
        description=None,
        asset_type=AssetType.SUMMARY,
        status=AssetStatus.ACTIVE,
        mime_type="application/json",
        file_name="study-summary.json",
        file_extension="json",
        file_size=0,
        storage_path="",
        checksum="",
        source=AssetSource.GENERATED,
        version=1,
        tags=[],
        asset_metadata={"kind": "study_summary", "sections": []},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.RUNNING,
    )
    session.add(report_asset)
    await session.commit()

    with pytest.raises(ReportNotReadyError):
        await ReportService(session).render_download(project.owner_id, report_asset.id, "docx")


@pytest.mark.asyncio
async def test_render_download_produces_docx_and_pdf_from_stored_sections(session, project):
    from app.modules.assets.ai_profile import AIProfile
    from app.modules.assets.enums import AssetSource, AssetStatus, AssetType
    from app.modules.assets.models import Asset

    report_asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Study Summary",
        description=None,
        asset_type=AssetType.SUMMARY,
        status=AssetStatus.ACTIVE,
        mime_type="application/json",
        file_name="study-summary.json",
        file_extension="json",
        file_size=0,
        storage_path="",
        checksum="",
        source=AssetSource.GENERATED,
        version=1,
        tags=[],
        asset_metadata={
            "kind": "study_summary",
            "sections": [{"title": "Key Themes", "content": "Poultry disease.", "covered": True, "citations": ["a1"]}],
        },
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.COMPLETED,
    )
    session.add(report_asset)
    await session.commit()

    docx_content, docx_media, docx_name = await ReportService(session).render_download(
        project.owner_id, report_asset.id, "docx"
    )
    pdf_content, pdf_media, pdf_name = await ReportService(session).render_download(
        project.owner_id, report_asset.id, "pdf"
    )

    assert docx_media == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert docx_name.endswith(".docx")
    assert len(docx_content) > 0
    assert pdf_media == "application/pdf"
    assert pdf_name.endswith(".pdf")
    assert len(pdf_content) > 0
