"""Per-section failure handling in `QwenDocumentUnderstandingService.analyze`."""

import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.modules.assets.processing.document_understanding import (
    DocumentUnderstandingError,
    QwenDocumentUnderstandingService,
)

_GOOD = json.dumps({"summary": "A section.", "keywords": ["k"], "language": "en"})


class _FakeGateway:
    """Returns cut-off JSON for any section containing the marker word."""

    def __init__(self, bad_marker: str) -> None:
        self._bad_marker = bad_marker

    async def generate(self, *, prompt: str, **_: object) -> SimpleNamespace:
        content = '{"summary": "cut off mid' if self._bad_marker in prompt else _GOOD
        return SimpleNamespace(content=content)


def _document(*markers: str) -> str:
    size = settings.qwen_max_input_characters
    return " ".join((marker + " ") * (size // (len(marker) + 1)) for marker in markers)


@pytest.mark.asyncio
async def test_unusable_section_is_skipped_not_fatal() -> None:
    service = QwenDocumentUnderstandingService(gateway=_FakeGateway("bibliography"))

    result = await service.analyze(_document("intro", "bibliography", "results"))

    assert result.summary == "A section. A section."
    assert result.processed_sections == 2
    assert result.total_sections == 3
    assert result.truncated is True


@pytest.mark.asyncio
async def test_all_sections_unusable_raises() -> None:
    service = QwenDocumentUnderstandingService(gateway=_FakeGateway("bibliography"))

    with pytest.raises(DocumentUnderstandingError):
        await service.analyze(_document("bibliography", "bibliography"))
