"""Sprint 12.1: pipeline-level tests proving extraction provenance
actually reaches persisted `KnowledgeChunk` rows, not just the
extractor unit tests in isolation (`test_extractors.py`).

Offline and deterministic like the rest of this file's siblings: Qwen
and embeddings are both faked (see `test_document_understanding.py`'s
own docstring for why real Ollama is reserved for exactly one
`@pytest.mark.live_ollama` test elsewhere) — what's under test here is
extract -> chunk -> persist, not the AI steps layered on top of it.
"""

import io
from pathlib import Path
import uuid
from typing import Any

import pytest

from app.core.config import settings
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.processing.document_understanding import (
    QwenDocumentMetadata,
    QwenDocumentUnderstandingService,
)
from app.modules.assets.processing.pipeline import AssetProcessingService
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import StorageProvider
from app.modules.knowledge_base.embeddings import NullEmbeddingProvider
from app.modules.knowledge_base.repository import KnowledgeChunkRepository

pytestmark = pytest.mark.asyncio


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
    """Skips the real Qwen call entirely -- this file tests extraction
    and chunk provenance, not document understanding."""

    def __init__(self) -> None:  # no gateway needed, never used
        pass

    async def analyze(self, text: str):
        from app.modules.assets.processing.document_understanding import (
            DocumentUnderstandingError,
        )

        raise DocumentUnderstandingError("skipped in this test")


class _SuccessfulUnderstanding(QwenDocumentUnderstandingService):
    def __init__(self) -> None:
        self.analyzed_sections: list[str] = []

    async def _analyze_section(self, text: str) -> QwenDocumentMetadata:
        self.analyzed_sections.append(text)
        return QwenDocumentMetadata(
            summary="Section summary.",
            keywords=[],
            entities=[],
            topics=[],
            language="en",
        )


async def test_document_understanding_reports_truncated_section_counts(monkeypatch):
    service = _SuccessfulUnderstanding()
    monkeypatch.setattr(settings, "qwen_max_input_characters", 10)
    monkeypatch.setattr(settings, "qwen_max_sections", 2)

    result = await service.analyze("x" * 25)

    assert result.truncated is True
    assert result.processed_sections == 2
    assert result.total_sections == 3
    assert len(service.analyzed_sections) == 2


def _make_asset(**overrides: Any) -> Asset:
    defaults: dict[str, Any] = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        title="Test Document",
        description=None,
        asset_type=AssetType.DOCUMENT,
        status=AssetStatus.ACTIVE,
        mime_type="text/plain",
        file_name="test.txt",
        file_extension=".txt",
        file_size=1,
        storage_path="unused",
        checksum="unused",
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=None,
        processing_status=AssetProcessingStatus.PENDING,
    )
    defaults.update(overrides)
    return Asset(**defaults)


async def _process(session, storage, asset) -> Asset:
    pipeline = AssetProcessingService(
        session,
        storage,
        chunk_size=1000,
        chunk_overlap=100,
        understanding=_NoOpUnderstanding(),
        embeddings=NullEmbeddingProvider(),
    )
    await pipeline.process_asset(asset.id)
    return await AssetRepository(session).get_by_id(asset.id)


def _make_pdf(pages: list[str]) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(300, 300))
    for text in pages:
        if text:
            pdf.drawString(20, 150, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _make_text_image(text: str | None) -> bytes:
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont

    img = PILImage.new("RGB", (900, 200), color="white")
    if text:
        import reportlab

        font = ImageFont.truetype(str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"), 28)
        ImageDraw.Draw(img).text((20, 20), text, fill="black", font=font)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _make_scanned_pdf(pages: list[str | None]) -> bytes:
    """A PDF where each page is a real embedded image (Sprint 12.5),
    matching an actual scanned PDF's shape -- unlike `_make_pdf`
    above, which produces genuinely content-less pages for an empty
    string, not image-bearing ones."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(900, 200))
    for text in pages:
        image_bytes = _make_text_image(text)
        pdf.drawImage(ImageReader(io.BytesIO(image_bytes)), 0, 0, width=900, height=200)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _make_xlsx(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def test_pdf_pages_reach_knowledge_chunks_with_page_numbers(session, project):
    storage = _FakeStorage()
    content = _make_pdf(["Content unique to page one", "Content unique to page two", "Content unique to page three"])
    storage_path = await storage.save(project_id=project.id, filename="report.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="report.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.COMPLETED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 3
    page_numbers = sorted(c.page_number for c in chunks)
    assert page_numbers == [1, 2, 3]
    by_page = {c.page_number: c.content for c in chunks}
    assert "page one" in by_page[1]
    assert "page two" in by_page[2]
    assert "page three" in by_page[3]


async def test_xlsx_rows_reach_knowledge_chunks_with_sheet_name(session, project):
    storage = _FakeStorage()
    content = _make_xlsx([["Product", "Revenue"], ["Laptop", 50000], ["Phone", 30000]])
    storage_path = await storage.save(project_id=project.id, filename="sales.xlsx", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_name="sales.xlsx",
        file_extension=".xlsx",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.COMPLETED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 2
    assert all(c.sheet_name == "Sales" for c in chunks)
    contents = {c.content for c in chunks}
    assert any(
        "Sheet: Sales" in content and "Product: Laptop" in content and "Revenue: 50000" in content
        for content in contents
    )


async def test_content_less_pdf_marks_failed_not_completed(session, project):
    """Sprint 12.5: pages with no text AND no embedded image at all
    (these `_make_pdf` fixtures for an empty string) have nothing for
    OCR to work with. With OCR available, this is a genuine extraction
    failure -- see `test_scanned_pdf_marks_completed_via_ocr` and
    `test_ocr_disabled_marks_ocr_required` below for the real
    scanned-page (has an image) cases this test used to stand in for."""
    storage = _FakeStorage()
    content = _make_pdf(["", "", ""])  # every page genuinely blank, no images
    storage_path = await storage.save(project_id=project.id, filename="blank.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="blank.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.FAILED
    assert "OCR" in refreshed.processing_error

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert chunks == [], "no chunks must be created for a document with zero usable content"


@pytest.mark.requires_tesseract
async def test_scanned_pdf_marks_completed_via_ocr(session, project):
    """Sprint 12.5: a genuinely scanned page (a real embedded image
    with readable text) must now reach COMPLETED, with OCR-recovered
    text persisted to a real KnowledgeChunk carrying the correct page
    number -- the actual OCR happy path, proven at the pipeline level,
    not just in `test_extractors.py`'s extractor-only tests."""
    storage = _FakeStorage()
    content = _make_scanned_pdf(["Scanned invoice total: four hundred dollars."])
    storage_path = await storage.save(project_id=project.id, filename="scan.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="scan.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.COMPLETED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert "Scanned invoice total" in chunks[0].content


async def test_ocr_disabled_marks_ocr_required(session, project, monkeypatch):
    """When OCR is unavailable on this deployment, a real scanned page
    (has an image, just can't be read by this deployment) must still
    be reported as OCR_REQUIRED, not FAILED -- the pre-12.5 outcome,
    unchanged for a deployment without OCR."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ocr_enabled", False)

    storage = _FakeStorage()
    content = _make_scanned_pdf(["Readable scanned text."])
    storage_path = await storage.save(project_id=project.id, filename="scan2.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="scan2.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.OCR_REQUIRED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert chunks == []


async def test_majority_content_less_pdf_marks_failed_despite_some_real_text(session, project):
    """Sprint 12.3's original regression guard, updated for Sprint
    12.5 (see `test_content_less_pdf_marks_failed_not_completed`'s
    docstring for why blank-with-no-image pages now map to FAILED)."""
    storage = _FakeStorage()
    content = _make_pdf(["Cover page with real text", "", "", "", ""])
    storage_path = await storage.save(project_id=project.id, filename="mostly-blank.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="mostly-blank.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.FAILED
    assert "have no usable text even after OCR" in refreshed.processing_error

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert chunks == [], "a majority-content-less document must not be indexed on its small readable fraction"


async def test_corrupted_pdf_marks_failed_not_completed(session, project):
    storage = _FakeStorage()
    content = b"%PDF-1.4\nnot a real pdf body"
    storage_path = await storage.save(project_id=project.id, filename="broken.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="broken.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.FAILED
    assert refreshed.processing_error

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert chunks == []


async def test_reprocessing_replaces_chunks_not_accumulates(session, project):
    """Idempotency (already guaranteed by `replace_chunks_for_asset`)
    must hold for the new provenance-carrying path too."""
    storage = _FakeStorage()
    content = _make_pdf(["Version one content"])
    storage_path = await storage.save(project_id=project.id, filename="doc.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name="doc.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    await _process(session, storage, asset)
    await _process(session, storage, asset)

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# OSD rotation correction + GIF/WEBP (Sprint 12.5 Phase 3), proven at the
# pipeline level -- real AssetProcessingService, real chunk persistence,
# not just extractor-only tests.
# ---------------------------------------------------------------------------


def _make_rotated_scanned_pdf(text: str, angle: int) -> bytes:
    from PIL import Image as PILImage
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    image_bytes = _make_text_image(text)
    image = PILImage.open(io.BytesIO(image_bytes))
    rotated = image.rotate(angle, expand=True, fillcolor="white")
    buf = io.BytesIO()
    rotated.save(buf, format="PNG")

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(900, 200))
    pdf.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 0, 0, width=900, height=200)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.mark.requires_tesseract
@pytest.mark.parametrize("angle", [90, 180, 270])
async def test_rotated_scanned_pdf_marks_completed_with_correct_text(session, project, angle):
    """The real defect Phase 2 found: a 90-/180-degree-rotated scanned
    page previously produced completely wrong text that still passed
    the confidence gate, indexing garbage as a 'successful' chunk. Must
    now reach COMPLETED with the actual correct text persisted."""
    storage = _FakeStorage()
    text = "Scanned invoice total: four hundred dollars."
    content = _make_rotated_scanned_pdf(text, angle)
    storage_path = await storage.save(project_id=project.id, filename=f"rotated_{angle}.pdf", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="application/pdf",
        file_name=f"rotated_{angle}.pdf",
        file_extension=".pdf",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.COMPLETED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert text in chunks[0].content, f"rotation={angle}: expected correct text, got {chunks[0].content!r}"


@pytest.mark.requires_tesseract
async def test_gif_marks_completed_via_ocr(session, project):
    from PIL import Image as PILImage

    storage = _FakeStorage()
    text = "GIF asset readable text."
    png_bytes = _make_text_image(text)
    frame = PILImage.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    frame.save(buf, format="GIF")
    content = buf.getvalue()

    storage_path = await storage.save(project_id=project.id, filename="scan.gif", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="image/gif",
        file_name="scan.gif",
        file_extension=".gif",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.COMPLETED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 1
    assert chunks[0].page_number is None
    assert text in chunks[0].content


@pytest.mark.requires_tesseract
async def test_webp_marks_completed_via_ocr(session, project):
    from PIL import Image as PILImage

    storage = _FakeStorage()
    text = "WEBP asset readable text."
    png_bytes = _make_text_image(text)
    image = PILImage.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="WEBP")
    content = buf.getvalue()

    storage_path = await storage.save(project_id=project.id, filename="scan.webp", content=content)
    asset = _make_asset(
        project_id=project.id,
        owner_id=project.owner_id,
        storage_path=storage_path,
        mime_type="image/webp",
        file_name="scan.webp",
        file_extension=".webp",
        file_size=len(content),
    )
    session.add(asset)
    await session.flush()
    await session.commit()

    refreshed = await _process(session, storage, asset)
    assert refreshed.processing_status is AssetProcessingStatus.COMPLETED

    chunks = await KnowledgeChunkRepository(session).list_by_project(project.id, asset_id=asset.id)
    assert len(chunks) == 1
    assert chunks[0].page_number is None
    assert text in chunks[0].content
