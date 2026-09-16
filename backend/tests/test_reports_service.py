"""Reports module: ownership, the no-processed-documents guard, and the
report repository's document query (Milestone 10 step 4 -- spec
sections 4 and 5)."""

import uuid

import pytest

from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
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


@pytest.mark.asyncio
async def test_llm_failure_marks_the_report_asset_failed_with_a_scrubbed_error(session, project, monkeypatch):
    from app.agents.reports.nodes import ReportGraphDependencies
    from app.modules.assets.ai_profile import AIProfile
    from app.modules.assets.enums import AssetSource
    from app.workers.tasks import _generate_report

    session.add(_make_asset(project))
    await session.commit()

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
    report_asset_id = report_asset.id

    class _RaisingGateway:
        async def generate(self, **kwargs):
            raise RuntimeError("https://secret-endpoint.example/leak?key=super-secret")

    class _EmptyDocumentLister:
        async def list_processed(self, project_id):
            return [{"asset_id": "a1", "title": "t", "file_name": "f.pdf", "summary": "s", "topics": []}]

    class _AllEvidenceSearcher:
        async def search(self, *, owner_id, project_id, query, limit):
            return [{"asset_id": "a1", "title": "t", "file_name": "f.pdf", "snippet": "evidence"}]

    monkeypatch.setattr(
        "app.workers.tasks.build_report_dependencies",
        lambda session: ReportGraphDependencies(
            document_lister=_EmptyDocumentLister(),
            section_searcher=_AllEvidenceSearcher(),
            llm_gateway=_RaisingGateway(),
        ),
    )

    await _generate_report(report_asset_id)

    from app.database.session import async_session_factory

    async with async_session_factory() as verify_session:
        refreshed = await AssetRepository(verify_session).get_by_id(report_asset_id)
        assert refreshed.processing_status.value == "failed"
        assert "secret-endpoint" not in refreshed.processing_error
        assert "super-secret" not in refreshed.processing_error
        assert refreshed.processing_error == "Report generation failed (RuntimeError)."


@pytest.mark.asyncio
async def test_a_db_error_mid_generation_still_marks_the_report_failed(session, project, monkeypatch):
    """Final-review finding I1: `_generate_report`'s `except` block used
    to set FAILED and commit on the SAME session a broken-transaction
    error (e.g. from the KB searcher) had already poisoned -- the final
    commit then raised `PendingRollbackError`, which escaped the
    handler entirely and left the asset stuck at `running` forever. A
    searcher that actually executes a failing SQL statement on the
    session (not a mock that merely raises in Python) reproduces the
    real "session unusable until rolled back" state; the fix must roll
    back before writing FAILED."""
    from sqlalchemy import text

    from app.agents.reports.nodes import ReportGraphDependencies
    from app.workers.tasks import _generate_report

    session.add(_make_asset(project))
    await session.commit()

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
    report_asset_id = report_asset.id

    class _EmptyDocumentLister:
        async def list_processed(self, project_id):
            return []

    class _BrokenTransactionSearcher:
        """Runs an invalid SQL statement directly on the shared session,
        so the underlying transaction is genuinely poisoned -- exactly
        the failure mode a mock that merely `raise`s in Python cannot
        reproduce."""

        def __init__(self, db_session) -> None:
            self._session = db_session

        async def search(self, *, owner_id, project_id, query, limit):
            await self._session.execute(text("SELECT * FROM this_table_does_not_exist"))
            return []

    monkeypatch.setattr(
        "app.workers.tasks.build_report_dependencies",
        lambda worker_session: ReportGraphDependencies(
            document_lister=_EmptyDocumentLister(),
            section_searcher=_BrokenTransactionSearcher(worker_session),
            llm_gateway=None,
        ),
    )

    await _generate_report(report_asset_id)

    from app.database.session import async_session_factory

    async with async_session_factory() as verify_session:
        refreshed = await AssetRepository(verify_session).get_by_id(report_asset_id)
        assert refreshed.processing_status.value == "failed"
        assert refreshed.processing_error is not None
        assert refreshed.processing_error.startswith("Report generation failed (")


class _OneDocumentLister:
    async def list_processed(self, project_id):
        return [{"asset_id": "a1", "title": "t", "file_name": "f.pdf", "summary": "s", "topics": []}]


class _AllEvidenceSearcher:
    async def search(self, *, owner_id, project_id, query, limit):
        return [{"asset_id": "a1", "title": "t", "file_name": "f.pdf", "snippet": "evidence"}]


class _Gateway:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls = 0

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return LLMResponse(
            content='{"content": "Section text.", "cited_asset_ids": ["a1"]}',
            model="fake-model",
            provider="fake",
            latency_ms=5,
        )


def _patch_dependencies(monkeypatch, gateway) -> None:
    from app.agents.reports.nodes import ReportGraphDependencies

    monkeypatch.setattr(
        "app.workers.tasks.build_report_dependencies",
        lambda worker_session: ReportGraphDependencies(
            document_lister=_OneDocumentLister(),
            section_searcher=_AllEvidenceSearcher(),
            llm_gateway=gateway,
        ),
    )


async def _steps_for(asset_id):
    from app.database.session import async_session_factory
    from app.modules.research.repository import ResearchStepRepository

    async with async_session_factory() as verify_session:
        return await ResearchStepRepository(verify_session).list_by_asset(asset_id)


@pytest.mark.asyncio
async def test_a_completed_report_run_persists_one_completed_step_per_node(project, monkeypatch, make_report_asset):
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    _patch_dependencies(monkeypatch, _Gateway())

    await _generate_report(report.id)

    steps = await _steps_for(report.id)
    assert [step.node_name for step in steps] == ["collect_documents", "retrieve_evidence", "write_sections", "coverage_check"]
    assert [step.step_index for step in steps] == [0, 1, 2, 3]
    assert {step.attempt for step in steps} == {1}
    for step in steps:
        assert step.status.value == "completed"
        assert step.summary
        assert step.duration_ms is not None
        assert step.error_message is None
        assert step.run_id is None


@pytest.mark.asyncio
async def test_a_failed_node_marks_its_step_failed_scrubbed_and_skips_the_rest(project, monkeypatch, make_report_asset):
    from app.database.session import async_session_factory
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    _patch_dependencies(monkeypatch, _Gateway(raises=RuntimeError("https://secret.example/?key=super-secret")))

    await _generate_report(report.id)

    steps = await _steps_for(report.id)
    assert [(step.node_name, step.status.value) for step in steps] == [
        ("collect_documents", "completed"),
        ("retrieve_evidence", "completed"),
        ("write_sections", "failed"),
        ("coverage_check", "skipped"),
    ]
    failed = steps[2]
    assert failed.error_message == "Report generation failed (RuntimeError)."
    assert steps[3].summary == "Not run: an earlier step failed."

    async with async_session_factory() as verify_session:
        refreshed = await AssetRepository(verify_session).get_by_id(report.id)
    assert refreshed.processing_status.value == "failed"
    assert refreshed.asset_metadata["sections"] == []


@pytest.mark.asyncio
async def test_get_report_returns_the_asset_with_its_steps_and_hides_it_from_others(session, project, make_report_asset):
    from datetime import datetime, timezone

    from app.modules.reports.schemas import ReportRead
    from app.modules.research.enums import ResearchStepStatus
    from app.modules.research.models import ResearchStep

    report = await make_report_asset(project, status=AssetProcessingStatus.COMPLETED)
    session.add(
        ResearchStep(
            asset_id=report.id,
            step_index=0,
            node_name="collect_documents",
            title="Collect project documents",
            status=ResearchStepStatus.COMPLETED,
            summary="Collected 1 processed document(s).",
            completed_at=datetime.now(timezone.utc),
            duration_ms=4,
        )
    )
    await session.commit()

    asset, steps = await ReportService(session).get_report(project.owner_id, report.id)
    read = ReportRead.from_report(asset, steps)

    assert read.id == report.id
    assert [step.node_name for step in read.steps] == ["collect_documents"]
    with pytest.raises(ReportNotFoundError):
        await ReportService(session).get_report(uuid.uuid4(), report.id)


@pytest.mark.asyncio
async def test_a_redelivered_generation_message_does_nothing(project, monkeypatch, make_report_asset):
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    gateway = _Gateway()
    _patch_dependencies(monkeypatch, gateway)

    first = await _generate_report(report.id)
    calls_after_first = gateway.calls
    steps_after_first = len(await _steps_for(report.id))
    second = await _generate_report(report.id)

    assert first["status"] == "ok"
    assert second == {"status": "skipped", "asset_id": str(report.id)}
    assert gateway.calls == calls_after_first
    assert len(await _steps_for(report.id)) == steps_after_first


@pytest.mark.asyncio
async def test_a_retried_report_records_its_second_attempt_separately(session, project, monkeypatch, make_report_asset):
    from app.workers.tasks import _generate_report

    monkeypatch.setattr("app.modules.reports.service.generate_report", type("_T", (), {"delay": staticmethod(lambda asset_id: None)}))
    report = await make_report_asset(project)

    _patch_dependencies(monkeypatch, _Gateway(raises=RuntimeError("boom")))
    await _generate_report(report.id)
    # `_generate_report` commits on its own session; `expire_on_commit`
    # is False, so `session`'s identity-mapped copy of `report` needs an
    # explicit refresh to see that session's write before this session's
    # own `get_owned_report` reads it back.
    await session.refresh(report)
    await ReportService(session).retry_report(project.owner_id, report.id)
    _patch_dependencies(monkeypatch, _Gateway())
    await _generate_report(report.id)

    steps = await _steps_for(report.id)
    first = [step for step in steps if step.attempt == 1]
    second = [step for step in steps if step.attempt == 2]
    assert [step.status.value for step in first] == ["completed", "completed", "failed", "skipped"]
    assert [step.status.value for step in second] == ["completed"] * 4
    assert [step.step_index for step in second] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_claim_pending_succeeds_exactly_once(session, project, make_report_asset):
    report = await make_report_asset(project)
    repository = ReportRepository(session)

    assert await repository.claim_pending(report.id) is True
    await session.commit()
    assert await repository.claim_pending(report.id) is False


@pytest.mark.asyncio
async def test_generating_a_report_deleted_before_it_could_be_claimed_is_skipped(session, project, make_report_asset):
    """A deleted report asset's row is gone, so `claim_pending` cannot
    find it and claims nothing -- the plain redelivered-message path."""
    from sqlalchemy import delete

    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    report_id = report.id
    await session.execute(delete(Asset).where(Asset.id == report_id))
    await session.commit()

    result = await _generate_report(report_id)

    assert result == {"status": "skipped", "asset_id": str(report_id)}
    assert await _steps_for(report_id) == []


@pytest.mark.asyncio
async def test_asset_deleted_between_claim_and_fetch_is_handled_cleanly(session, project, monkeypatch, make_report_asset):
    """Final-review finding I1: the asset row can be deleted (project or
    user cascade) in the gap between a successful `claim_pending` and the
    following `get_by_id`. `_generate_report` must return cleanly instead
    of raising `AttributeError` on `asset.id`, and must write no steps for
    an asset that no longer exists."""
    from app.modules.assets.repository import AssetRepository
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    report_id = report.id

    real_get_by_id = AssetRepository.get_by_id

    async def _missing_after_claim(self, asset_id):
        if asset_id == report_id:
            return None
        return await real_get_by_id(self, asset_id)

    monkeypatch.setattr(AssetRepository, "get_by_id", _missing_after_claim)

    result = await _generate_report(report_id)

    assert result == {"status": "asset_missing", "asset_id": str(report_id)}
    assert await _steps_for(report_id) == []


@pytest.mark.asyncio
async def test_retry_resets_a_failed_report_and_re_enqueues_it(session, project, monkeypatch, make_report_asset):
    dispatched: list[str] = []
    monkeypatch.setattr(
        "app.modules.reports.service.generate_report",
        type("_T", (), {"delay": staticmethod(lambda asset_id: dispatched.append(asset_id))}),
    )
    report = await make_report_asset(
        project,
        status=AssetProcessingStatus.FAILED,
        processing_error="Report generation failed (RuntimeError).",
    )

    retried = await ReportService(session).retry_report(project.owner_id, report.id)

    assert retried.processing_status is AssetProcessingStatus.PENDING
    assert retried.processing_error is None
    assert retried.asset_metadata["sections"] == []
    assert dispatched == [str(report.id)]


@pytest.mark.asyncio
async def test_retry_refuses_a_report_that_has_not_failed_and_hides_it_from_others(session, project, monkeypatch, make_report_asset):
    from app.modules.reports.service import ReportNotRetryableError

    monkeypatch.setattr("app.modules.reports.service.generate_report", type("_T", (), {"delay": staticmethod(lambda asset_id: None)}))
    report = await make_report_asset(project, status=AssetProcessingStatus.COMPLETED)

    with pytest.raises(ReportNotRetryableError):
        await ReportService(session).retry_report(project.owner_id, report.id)
    with pytest.raises(ReportNotFoundError):
        await ReportService(session).retry_report(uuid.uuid4(), report.id)
