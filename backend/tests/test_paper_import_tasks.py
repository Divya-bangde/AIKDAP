"""Milestone 10 step 3 (Add & re-run): the per-paper import task and
the chord's finalize callback, called as plain async functions (this
codebase's established convention -- see `test_research_citations.py`
calling `ResearchExecutionService(session).execute(...)` directly
rather than through Celery's eager mode). No live network: the
downloader is monkeypatched at the module level `tasks.py` imports it
from.
"""

import io
import uuid

import pytest

from app.modules.assets.enums import AssetProcessingStatus, AssetSource
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import StorageProvider
from app.modules.knowledge_base.embeddings import NullEmbeddingProvider
from app.modules.assets.processing.document_understanding import (
    DocumentUnderstandingError,
    QwenDocumentUnderstandingService,
)
from app.modules.assets.processing.pipeline import AssetProcessingService
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
from app.modules.research.paper_import import PaperDownloadError
from app.modules.research.repository import ResearchRunRepository
from app.workers import tasks as tasks_module

pytestmark = pytest.mark.asyncio


def _make_pdf_bytes() -> bytes:
    """A genuinely parseable one-page PDF (magic bytes alone pass the
    upload validator's content-matches-mime check, but the real
    extract/chunk/embed pipeline this task runs inline needs actual
    extractable text) -- same `reportlab` convention as `_make_pdf` in
    `test_asset_processing_pipeline.py`."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(300, 300))
    pdf.drawString(20, 150, "Content unique to the imported paper")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


_PDF_BYTES = _make_pdf_bytes()


class _FakeStorage(StorageProvider):
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def save(self, *, project_id, filename, content: bytes) -> str:
        path = f"{project_id}/{filename}"
        self._files[path] = content
        return path

    async def read(self, storage_path: str) -> bytes:
        return self._files[storage_path]

    async def delete(self, storage_path: str) -> None:
        self._files.pop(storage_path, None)

    def exists(self, storage_path: str) -> bool:
        return storage_path in self._files


class _NoOpUnderstanding(QwenDocumentUnderstandingService):
    def __init__(self) -> None:
        pass

    async def analyze(self, text: str):
        raise DocumentUnderstandingError("skipped in this test")


def _fake_processing_service(session, storage):
    """Real extract -> chunk -> embed, with Qwen/embeddings faked out --
    same convention as `test_asset_processing_pipeline.py`."""
    return AssetProcessingService(
        session,
        storage,
        chunk_size=500,
        chunk_overlap=50,
        understanding=_NoOpUnderstanding(),
        embeddings=NullEmbeddingProvider(),
    )


def _paper(**overrides) -> dict:
    return {
        "openalex_id": "https://openalex.org/W1",
        "title": "A Suggested Paper",
        "oa_pdf_url": "https://example.org/paper.pdf",
        **overrides,
    }


async def _make_run(session, project, **overrides) -> ResearchRun:
    run = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query=overrides.pop("query", "A research question?"),
        status=ResearchRunStatus.COMPLETED,
        include_assets=True,
        include_web=True,
        max_results=5,
        **overrides,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


@pytest.fixture(autouse=True)
def _fakes(session, monkeypatch):
    storage = _FakeStorage()
    monkeypatch.setattr(tasks_module, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(
        tasks_module, "get_asset_processing_service", _fake_processing_service
    )
    monkeypatch.setattr(
        tasks_module, "async_session_factory", lambda: _SameSessionContext(session)
    )
    return storage


class _SameSessionContext:
    """Test double for `async_session_factory()`'s `async with` usage --
    hands back the shared test `session` instead of opening a real new
    one, so every task call in a test sees the same transactional state
    the test itself set up (matching `test_asset_processing_pipeline.py`
    convention adapted to `async with`)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


async def test_import_paper_downloads_validates_processes_and_marks_added(
    session, project, monkeypatch
):
    async def fake_download(url, **kwargs):
        assert url == "https://example.org/paper.pdf"
        return _PDF_BYTES

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)

    run = await _make_run(
        session, project, suggested_papers=[_paper()], parent_run_id=None
    )

    result = await tasks_module._import_paper(run.id, _paper())

    assert result["status"] == "added"
    assert result["asset_id"] is not None

    asset = await AssetRepository(session).get_by_id(uuid.UUID(result["asset_id"]))
    assert asset.source is AssetSource.IMPORTED
    assert asset.processing_status is AssetProcessingStatus.COMPLETED

    await session.refresh(run)
    entry = run.suggested_papers[0]
    assert entry["import_status"] == "added"
    assert entry["imported_asset_id"] == result["asset_id"]


async def test_import_paper_marks_failed_on_download_error(session, project, monkeypatch):
    async def fake_download(url, **kwargs):
        raise PaperDownloadError("Downloaded content is not a PDF.")

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)

    run = await _make_run(session, project, suggested_papers=[_paper()])

    result = await tasks_module._import_paper(run.id, _paper())

    assert result["status"] == "failed"
    assert result["reason"] == "Downloaded content is not a PDF."

    await session.refresh(run)
    assert run.suggested_papers[0]["import_status"] == "failed"
    assert run.suggested_papers[0]["import_error"] == "Downloaded content is not a PDF."


async def test_import_paper_sets_processing_status_before_downloading(
    session, project, monkeypatch
):
    """The per-paper status must reach `processing` even if the download
    itself later fails -- a card frozen on `queued` forever would look
    hung."""
    seen_mid_download: dict = {}

    async def fake_download(url, **kwargs):
        run = await ResearchRunRepository(session).get_by_id(run_id_holder["id"])
        seen_mid_download["status"] = run.suggested_papers[0]["import_status"]
        raise PaperDownloadError("boom")

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)

    run = await _make_run(session, project, suggested_papers=[_paper()])
    run_id_holder = {"id": run.id}

    await tasks_module._import_paper(run.id, _paper())

    assert seen_mid_download["status"] == "processing"


async def test_finalize_import_starts_exactly_one_rerun_on_partial_success(
    session, project, monkeypatch
):
    dispatched: list[str] = []
    monkeypatch.setattr(
        tasks_module.execute_research_run, "delay", lambda run_id: dispatched.append(run_id)
    )

    original = await _make_run(
        session,
        project,
        query="Original question?",
        suggested_papers=[
            {**_paper(openalex_id="W1"), "import_status": "added"},
            {**_paper(openalex_id="W2"), "import_status": "failed"},
        ],
    )
    results = [
        {"openalex_id": "W1", "status": "added", "asset_id": "irrelevant", "reason": None},
        {"openalex_id": "W2", "status": "failed", "asset_id": None, "reason": "not a PDF"},
    ]

    outcome = await tasks_module._finalize_import(results, original.id)

    assert outcome["status"] == "rerun_started"
    assert len(dispatched) == 1

    child = await ResearchRunRepository(session).find_latest_child(original.id)
    assert child is not None
    assert child.query == "Original question?"
    assert child.parent_run_id == original.id
    assert str(child.id) == dispatched[0]


async def test_concurrent_paper_status_updates_do_not_clobber_each_other(session, project):
    """Final review Fix 1: `_update_paper_import_status` now reads the
    run with `get_by_id_for_update` (a `SELECT ... FOR UPDATE` row
    lock) so two per-paper Celery tasks racing on the same run's
    `suggested_papers` JSONB column cannot silently overwrite each
    other's already-written status.

    This test's harness shares one DB session across the whole test
    (see `_SameSessionContext` above), so it cannot simulate the actual
    interleaved-write race a real multi-connection concurrent commit
    would produce -- that's an accepted test-infrastructure limitation,
    not a gap in the fix; the row lock itself is what prevents that
    race in production. What this test DOES prove is the other half of
    correctness the fix depends on: calling
    `_update_paper_import_status` for two different papers on the same
    run, one after another, must leave BOTH entries at their own final
    status -- i.e. updating one paper's entry never clobbers a sibling
    entry already written by a previous call. That whole-list-rebuild
    correctness is what makes serializing the two calls (via the row
    lock) actually sufficient to fix the race, rather than merely
    moving it.
    """
    run = await _make_run(
        session, project, suggested_papers=[_paper(openalex_id="W1"), _paper(openalex_id="W2")]
    )

    await tasks_module._update_paper_import_status(session, run.id, "W1", status="added", asset_id="asset-1")
    await tasks_module._update_paper_import_status(session, run.id, "W2", status="failed", error="not a PDF")

    await session.refresh(run)
    by_id = {entry["openalex_id"]: entry for entry in run.suggested_papers}
    assert by_id["W1"]["import_status"] == "added"
    assert by_id["W1"]["imported_asset_id"] == "asset-1"
    assert by_id["W2"]["import_status"] == "failed"
    assert by_id["W2"]["import_error"] == "not a PDF"


async def test_import_paper_never_raises_on_an_unexpected_exception(session, project, monkeypatch):
    """Final review Fix 2: an exception outside the three known error
    types (download, validation, duplicate) must not propagate out of
    `_import_paper` -- it is a `max_retries=0` Celery chord member, and
    an unhandled exception there fails the whole chord, so
    `finalize_paper_import` never runs and the re-run silently never
    starts even when other papers succeeded."""

    async def fake_download(url, **kwargs):
        return _PDF_BYTES

    async def fake_process_asset(self, asset_id):
        raise RuntimeError("boom: unexpected failure with a secret url https://internal/x")

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)
    monkeypatch.setattr(AssetProcessingService, "process_asset", fake_process_asset)

    run = await _make_run(session, project, suggested_papers=[_paper()])

    result = await tasks_module._import_paper(run.id, _paper())

    assert result["status"] == "failed"
    assert result["reason"] == tasks_module._UNEXPECTED_IMPORT_ERROR
    assert "boom" not in result["reason"]

    await session.refresh(run)
    entry = run.suggested_papers[0]
    assert entry["import_status"] == "failed"
    assert entry["import_error"] == tasks_module._UNEXPECTED_IMPORT_ERROR
    assert "boom" not in entry["import_error"]
    assert "secret" not in entry["import_error"]


async def test_import_paper_treats_duplicate_asset_as_already_added(session, project, monkeypatch):
    """Final review Fix 3: a duplicate (checksum-matched) paper genuinely
    IS already in the project, so `_import_paper` must mark it `added`
    pointing at the existing asset -- not `failed`, which would
    silently regress a paper the project already has."""
    from app.modules.assets.service import AssetService, DuplicateAssetError
    from app.modules.assets.models import Asset
    from app.modules.assets.enums import AssetStatus, AssetType

    async def fake_download(url, **kwargs):
        return _PDF_BYTES

    existing_asset = Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title="Already Imported",
        asset_type=AssetType.DOCUMENT,
        status=AssetStatus.ACTIVE,
        mime_type="application/pdf",
        file_name="existing.pdf",
        file_extension="pdf",
        file_size=len(_PDF_BYTES),
        storage_path=f"{project.id}/existing.pdf",
        checksum="irrelevant",
        source=AssetSource.IMPORTED,
        version=1,
        tags=[],
        asset_metadata={},
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.COMPLETED,
    )
    session.add(existing_asset)
    await session.flush()

    async def fake_create_imported_asset(self, **kwargs):
        raise DuplicateAssetError(existing_asset)

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)
    monkeypatch.setattr(AssetService, "create_imported_asset", fake_create_imported_asset)

    run = await _make_run(session, project, suggested_papers=[_paper()])

    result = await tasks_module._import_paper(run.id, _paper())

    assert result["status"] == "added"
    assert result["asset_id"] == str(existing_asset.id)

    await session.refresh(run)
    entry = run.suggested_papers[0]
    assert entry["import_status"] == "added"
    assert entry["imported_asset_id"] == str(existing_asset.id)


async def test_finalize_import_starts_no_rerun_when_every_paper_failed(
    session, project, monkeypatch
):
    dispatched: list[str] = []
    monkeypatch.setattr(
        tasks_module.execute_research_run, "delay", lambda run_id: dispatched.append(run_id)
    )

    original = await _make_run(session, project)
    results = [
        {"openalex_id": "W1", "status": "failed", "asset_id": None, "reason": "not a PDF"},
        {"openalex_id": "W2", "status": "failed", "asset_id": None, "reason": "too large"},
    ]

    outcome = await tasks_module._finalize_import(results, original.id)

    assert outcome["status"] == "all_failed"
    assert dispatched == []
    assert await ResearchRunRepository(session).find_latest_child(original.id) is None
