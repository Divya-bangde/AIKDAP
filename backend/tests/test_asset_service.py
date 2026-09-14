"""Sprint 12.4: duplicate-upload protection.

No test file existed for `AssetService.upload()` before this sprint.
Covers the new per-project checksum dedup: `checksum` was computed and
stored (indexed) since Sprint 6/9 but never queried until this sprint
wired it up as a real defense against re-running extraction/Qwen/
embedding for byte-identical content already indexed.
"""

import io
import uuid
from dataclasses import dataclass

import pytest
from starlette.datastructures import Headers, UploadFile

from app.modules.assets.ai_profile import AIProfile, AIProfileStatus
from app.modules.assets.enums import (
    AssetProcessingStatus,
    AssetSource,
    AssetStatus,
    AssetType,
    EmbeddingStatus,
)
from app.modules.assets.models import Asset
from app.modules.assets.service import AssetNotFoundError, AssetService, DuplicateAssetError
from app.modules.assets.storage import StorageProvider
from app.modules.knowledge_base.models import KnowledgeChunk

pytestmark = pytest.mark.asyncio


@dataclass
class _FakeAsyncResult:
    id: str = "fake-task-id"


@pytest.fixture(autouse=True)
def _no_real_celery_dispatch(monkeypatch):
    """`AssetService.upload()` calls `process_uploaded_asset.delay(...)`
    at the end of a successful upload -- against the real broker, since
    no test-mode Celery config exists in this project (matching
    `test_orchestrator.py`'s equivalent pattern of never calling a
    `.delay()`-dispatching entry point directly). Stubbed here so these
    service-level tests exercise dedup logic only, not the live
    worker/broker."""
    from app.modules.assets import service as service_module

    monkeypatch.setattr(
        service_module.process_uploaded_asset, "delay", lambda *a, **k: _FakeAsyncResult()
    )


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


def _upload_file(content: bytes, *, filename: str, content_type: str) -> UploadFile:
    return UploadFile(
        io.BytesIO(content),
        size=len(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def test_identical_upload_in_same_project_is_rejected(session, project):
    service = AssetService(session, _FakeStorage())
    content = b"identical content for dedup test"

    first = await service.upload(
        current_user_id=project.owner_id,
        project_id=project.id,
        file=_upload_file(content, filename="report.txt", content_type="text/plain"),
        title=None,
        description=None,
        asset_type=None,
        tags=[],
    )
    assert first.processing_status is not None

    with pytest.raises(DuplicateAssetError) as exc_info:
        await service.upload(
            current_user_id=project.owner_id,
            project_id=project.id,
            file=_upload_file(content, filename="report-copy.txt", content_type="text/plain"),
            title=None,
            description=None,
            asset_type=None,
            tags=[],
        )
    assert exc_info.value.existing_asset.id == first.id


async def test_different_content_is_not_rejected(session, project):
    service = AssetService(session, _FakeStorage())

    await service.upload(
        current_user_id=project.owner_id,
        project_id=project.id,
        file=_upload_file(b"content A", filename="a.txt", content_type="text/plain"),
        title=None,
        description=None,
        asset_type=None,
        tags=[],
    )
    second = await service.upload(
        current_user_id=project.owner_id,
        project_id=project.id,
        file=_upload_file(b"content B", filename="b.txt", content_type="text/plain"),
        title=None,
        description=None,
        asset_type=None,
        tags=[],
    )
    assert second is not None


async def test_archived_duplicate_does_not_block_reupload(session, project):
    """An archived asset must not block a fresh re-upload of the same
    content -- only an ACTIVE duplicate does."""
    service = AssetService(session, _FakeStorage())
    content = b"content that gets archived"

    first = await service.upload(
        current_user_id=project.owner_id,
        project_id=project.id,
        file=_upload_file(content, filename="doc.txt", content_type="text/plain"),
        title=None,
        description=None,
        asset_type=None,
        tags=[],
    )
    first.status = AssetStatus.ARCHIVED
    await session.commit()

    second = await service.upload(
        current_user_id=project.owner_id,
        project_id=project.id,
        file=_upload_file(content, filename="doc-again.txt", content_type="text/plain"),
        title=None,
        description=None,
        asset_type=None,
        tags=[],
    )
    assert second.id != first.id


# ---------------------------------------------------------------------------
# Sprint 12.8: reprocessing dedup -- `AssetService.reprocess()` skips a
# genuinely already-fully-processed asset instead of re-queuing the
# Celery task, unless the caller asks to `force` it.
# ---------------------------------------------------------------------------


async def _make_asset(session, project, **overrides) -> Asset:
    defaults: dict = dict(
        id=uuid.uuid4(),
        project_id=project.id,
        owner_id=project.owner_id,
        title="Reprocess Test Asset",
        description=None,
        asset_type=AssetType.DOCUMENT,
        status=AssetStatus.ACTIVE,
        mime_type="text/plain",
        file_name="test.txt",
        file_extension=".txt",
        file_size=42,
        storage_path="unused",
        checksum=uuid.uuid4().hex,  # unique per asset, avoids dedup collisions
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=AssetProcessingStatus.COMPLETED,
    )
    defaults.update(overrides)
    asset = Asset(**defaults)
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def _add_chunk(session, asset: Asset, *, embedding_status: EmbeddingStatus) -> None:
    session.add(
        KnowledgeChunk(
            project_id=asset.project_id,
            asset_id=asset.id,
            chunk_index=0,
            content="chunk content",
            embedding_status=embedding_status,
        )
    )
    await session.commit()


def _completed_ai_profile() -> dict:
    profile = AIProfile(status=AIProfileStatus.COMPLETED, generated_by="qwen3.5:4b", summary="x")
    return profile.model_dump(mode="json")


async def test_unchanged_fully_processed_asset_is_skipped_not_requeued(session, project, monkeypatch):
    """A COMPLETED asset with completed embeddings and Qwen understanding
    must not be re-queued -- the core Sprint 12.8 behavior."""
    asset = await _make_asset(session, project, ai_profile=_completed_ai_profile())
    await _add_chunk(session, asset, embedding_status=EmbeddingStatus.COMPLETED)

    from app.modules.assets import service as service_module

    calls: list[str] = []
    monkeypatch.setattr(
        service_module.process_uploaded_asset, "delay", lambda asset_id: calls.append(asset_id)
    )

    service = AssetService(session, _FakeStorage())
    result = await service.reprocess(project.owner_id, asset.id)

    assert calls == []  # no Celery dispatch
    assert result.processing_status is AssetProcessingStatus.COMPLETED  # untouched


async def test_failed_asset_is_always_requeued(session, project):
    asset = await _make_asset(session, project, processing_status=AssetProcessingStatus.FAILED)

    service = AssetService(session, _FakeStorage())
    result = await service.reprocess(project.owner_id, asset.id)

    assert result.processing_status is AssetProcessingStatus.QUEUED


async def test_missing_embeddings_is_not_skipped(session, project):
    """COMPLETED processing and COMPLETED Qwen, but a chunk that never
    finished embedding, must still reprocess."""
    asset = await _make_asset(session, project, ai_profile=_completed_ai_profile())
    await _add_chunk(session, asset, embedding_status=EmbeddingStatus.FAILED)

    service = AssetService(session, _FakeStorage())
    result = await service.reprocess(project.owner_id, asset.id)

    assert result.processing_status is AssetProcessingStatus.QUEUED


async def test_missing_ai_understanding_is_not_skipped(session, project):
    """COMPLETED processing and embeddings, but Qwen never completed
    (still PENDING/FAILED/UNAVAILABLE), must still reprocess."""
    asset = await _make_asset(session, project)  # default AIProfile() is PENDING
    await _add_chunk(session, asset, embedding_status=EmbeddingStatus.COMPLETED)

    service = AssetService(session, _FakeStorage())
    result = await service.reprocess(project.owner_id, asset.id)

    assert result.processing_status is AssetProcessingStatus.QUEUED


async def test_force_reprocess_runs_even_when_fully_processed(session, project):
    asset = await _make_asset(session, project, ai_profile=_completed_ai_profile())
    await _add_chunk(session, asset, embedding_status=EmbeddingStatus.COMPLETED)

    service = AssetService(session, _FakeStorage())
    result = await service.reprocess(project.owner_id, asset.id, force=True)

    assert result.processing_status is AssetProcessingStatus.QUEUED


async def test_reprocess_skip_is_scoped_to_the_asset_not_the_project(session, project):
    """A second, incomplete asset in the same project must never make a
    fully-processed asset's check pass or fail incorrectly, and vice
    versa -- the completeness check must be scoped by `asset_id`."""
    complete = await _make_asset(session, project, ai_profile=_completed_ai_profile())
    await _add_chunk(session, complete, embedding_status=EmbeddingStatus.COMPLETED)

    incomplete = await _make_asset(
        session, project, processing_status=AssetProcessingStatus.FAILED
    )

    service = AssetService(session, _FakeStorage())
    complete_result = await service.reprocess(project.owner_id, complete.id)
    incomplete_result = await service.reprocess(project.owner_id, incomplete.id)

    assert complete_result.processing_status is AssetProcessingStatus.COMPLETED
    assert incomplete_result.processing_status is AssetProcessingStatus.QUEUED


async def test_reprocess_of_nonexistent_asset_raises(session, project):
    service = AssetService(session, _FakeStorage())
    with pytest.raises(AssetNotFoundError):
        await service.reprocess(project.owner_id, uuid.uuid4())


# ---------------------------------------------------------------------------
# Milestone 10, Task 3: `AssetService.create_imported_asset`
# ---------------------------------------------------------------------------

from app.modules.assets.validators import AssetValidationError

_PDF_BYTES = b"%PDF-1.4\nmock imported pdf\n"


async def test_create_imported_asset_persists_with_imported_source(session, project):
    service = AssetService(session, _FakeStorage())

    asset = await service.create_imported_asset(
        owner_id=project.owner_id,
        project_id=project.id,
        content=_PDF_BYTES,
        file_name="W123.pdf",
        mime_type="application/pdf",
        title="A Suggested Paper",
    )

    assert asset.source is AssetSource.IMPORTED
    assert asset.asset_type is AssetType.DOCUMENT
    assert asset.processing_status is AssetProcessingStatus.QUEUED
    assert asset.file_size == len(_PDF_BYTES)
    assert asset.title == "A Suggested Paper"


async def test_create_imported_asset_never_enqueues_processing(session, project, monkeypatch):
    """Unlike `upload()`, the caller runs the pipeline inline -- this
    method must never dispatch the Celery task itself, or the pipeline
    would run twice."""
    from app.modules.assets import service as service_module

    def _fail(*args, **kwargs):
        raise AssertionError("create_imported_asset must not enqueue process_uploaded_asset")

    monkeypatch.setattr(service_module.process_uploaded_asset, "delay", _fail)

    service = AssetService(session, _FakeStorage())
    await service.create_imported_asset(
        owner_id=project.owner_id,
        project_id=project.id,
        content=_PDF_BYTES,
        file_name="W456.pdf",
        mime_type="application/pdf",
        title="Another Paper",
    )


async def test_create_imported_asset_rejects_non_pdf_content_declared_as_pdf(session, project):
    service = AssetService(session, _FakeStorage())

    with pytest.raises(AssetValidationError):
        await service.create_imported_asset(
            owner_id=project.owner_id,
            project_id=project.id,
            content=b"<html>not a pdf</html>",
            file_name="W789.pdf",
            mime_type="application/pdf",
            title="Fake Paper",
        )


async def test_create_imported_asset_deduplicates_by_checksum(session, project):
    service = AssetService(session, _FakeStorage())
    await service.create_imported_asset(
        owner_id=project.owner_id,
        project_id=project.id,
        content=_PDF_BYTES,
        file_name="first.pdf",
        mime_type="application/pdf",
        title="First",
    )

    with pytest.raises(DuplicateAssetError):
        await service.create_imported_asset(
            owner_id=project.owner_id,
            project_id=project.id,
            content=_PDF_BYTES,
            file_name="duplicate.pdf",
            mime_type="application/pdf",
            title="Duplicate",
        )
