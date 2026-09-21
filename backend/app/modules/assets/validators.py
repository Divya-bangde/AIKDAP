"""Upload validation: MIME type, extension, and file size.

Ownership validation (a user may only act on assets within their own
projects) is authorization logic, not file validation, and lives in
`service.py` alongside the rest of the module's business rules.
"""

from pathlib import Path

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "text/markdown",
        "application/json",
        "text/html",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    }
)

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".txt",
        ".csv",
        ".md",
        ".json",
        ".html",
        ".htm",
        ".docx",
        ".xlsx",
        ".pptx",
        ".doc",
        ".xls",
        ".ppt",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
    }
)


#: Text-bearing formats a report or build plan can draw on; an upload of one
#: with no explicit type is a DOCUMENT rather than OTHER.
DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".txt", ".md", ".html", ".htm", ".docx", ".doc"}
)


class AssetValidationError(Exception):
    """Raised when an uploaded file fails MIME type, extension, or size checks."""


def validate_mime_type(mime_type: str | None) -> str:
    """Ensure the declared MIME type is permitted, returning it normalized."""
    resolved = (mime_type or "application/octet-stream").lower()
    if resolved not in ALLOWED_MIME_TYPES:
        raise AssetValidationError(f"MIME type '{resolved}' is not permitted.")
    return resolved


def validate_extension(file_name: str) -> str:
    """Ensure the file's extension is permitted, returning it lowercased."""
    extension = Path(file_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise AssetValidationError(f"File extension '{extension}' is not permitted.")
    return extension


#: Sprint 12.4: which declared MIME types are consistent with a given
#: extension. `validate_extension` and `validate_mime_type` each check
#: their own value is *individually* allowed, but neither checked that
#: the two *agree* -- a `.pdf`-named file declaring `text/plain` (or
#: any other individually-allowed pair) passed both checks and was then
#: routed to the wrong extractor by MIME type, since dispatch
#: (`get_text_extractor`) trusts `mime_type` alone. Confirmed
#: exploitable: a real 277KB production PDF decoded as UTF-8 text
#: produces mostly-printable PDF syntax (only ~0.3% control/replacement
#: characters for a small uncompressed sample, ~53% for a real
#: compressed one) that `PlainTextExtractor` would have accepted as
#: genuine content with no error at all before this sprint's
#: `_looks_like_binary_garbage` gate (see extractors.py) existed as a
#: second line of defense.
#:
#: Several extensions intentionally accept more than one MIME type:
#: real browsers/OSes are inconsistent about what they declare for
#: `.md`/`.csv`/`.json` (Windows commonly sends `text/plain` for all
#: three), so being stricter here would reject legitimate uploads this
#: sprint has no evidence justifies rejecting.
_EXTENSION_MIME_FAMILIES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/plain"}),
    ".csv": frozenset({"text/csv", "text/plain"}),
    ".json": frozenset({"application/json", "text/plain"}),
    ".html": frozenset({"text/html"}),
    ".htm": frozenset({"text/html"}),
    ".docx": frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ),
    ".xlsx": frozenset(
        {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    ),
    ".pptx": frozenset(
        {"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
    ),
    ".doc": frozenset({"application/msword"}),
    ".xls": frozenset({"application/vnd.ms-excel"}),
    ".ppt": frozenset({"application/vnd.ms-powerpoint"}),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".gif": frozenset({"image/gif"}),
    ".webp": frozenset({"image/webp"}),
    ".svg": frozenset({"image/svg+xml"}),
}


def validate_extension_matches_mime(extension: str, mime_type: str) -> None:
    """Reject a file whose extension and declared MIME type disagree.

    Both values individually being "allowed" is not the same as them
    being consistent with each other -- this closes that gap. A no-op
    for an extension this sprint has no mapping for, so a future format
    added to `ALLOWED_EXTENSIONS` without a matching entry here fails
    open rather than rejecting every upload of it.
    """
    expected = _EXTENSION_MIME_FAMILIES.get(extension)
    if expected is not None and mime_type not in expected:
        raise AssetValidationError(
            f"File extension '{extension}' does not match declared "
            f"content type '{mime_type}'."
        )


def validate_file_size(size_bytes: int, *, max_bytes: int) -> None:
    """Ensure the file is non-empty and does not exceed the configured maximum."""
    if size_bytes <= 0:
        raise AssetValidationError("Uploaded file is empty.")
    if size_bytes > max_bytes:
        raise AssetValidationError(
            f"File size ({size_bytes} bytes) exceeds the maximum of {max_bytes} bytes."
        )


#: Sprint 12.1: cheap magic-byte checks for the formats where the
#: declared MIME type was previously trusted outright (Phase 0 audit
#: finding: "MIME type is trusted from the browser, not verified
#: against actual bytes"). Not a full file-type sniffer (that would
#: need `libmagic`, a new system dependency) — just enough to catch an
#: arbitrary file renamed/relabeled as one of the formats real parsers
#: below will run against. DOCX/XLSX/PPTX share the same signature
#: because OOXML is a ZIP container; a mismatch there only proves "not
#: a ZIP at all", which the format-specific parser would reject anyway
#: with a less useful error, so checking it here gives an earlier,
#: clearer `AssetValidationError` instead of a confusing extraction
#: failure two pipeline stages later.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04",),
}


def validate_content_matches_mime(content: bytes, mime_type: str) -> None:
    """Reject a file whose bytes don't match its declared MIME type, for
    the formats where that's cheap to check. A no-op for every other
    MIME type — plain-text formats have no reliable magic bytes to
    check, and that's fine: they can't do anything a parser can exploit
    the way a mislabeled binary could."""
    signatures = _MAGIC_BYTES.get(mime_type)
    if signatures is None:
        return
    if not any(content.startswith(sig) for sig in signatures):
        raise AssetValidationError(
            f"File content does not match its declared type '{mime_type}'."
        )
