"""Map each PDF chunk to where its text sits on the page (click-to-source).

Runs after chunking and never replaces it: pypdf still extracts the
text, the chunker still splits it, and this module only locates each
finished chunk's text among the page's words as `pdfplumber` sees them.

Coordinates are PDF points with a top-left origin, so a client scales
them by `rendered_width / page_width` with no y-flip.

Known limitation: pages with a `/Rotate` are not located. pdfplumber
0.11 does not normalize rotation (words come back as per-character
fragments in an unrotated space), so every chunk on such a page is
stored as `match_quality="none"` and the viewer opens the page instead
of guessing at a highlight.

Matching, per chunk (a chunk never spans pages, see `chunker.py`, so
only its own page is searched):

1. both sides become one normalized character stream -- NFKC (folds
   ligatures), casefold, and only letters and digits kept, so word
   splitting, spacing and line-break hyphenation cannot differ;
2. `exact`: the chunk's whole stream occurs on the page;
3. `fuzzy`: its first and last ~12 words' worth of characters occur, in
   order, within a bounded distance -- tolerating a differing middle;
4. otherwise `none` (e.g. an OCR'd page, which has no text layer).

Words are read in content-stream order (`use_text_flow=True`), the same
order pypdf reads, so a two-column page's columns are not interleaved.
"""

import asyncio
import bisect
import io
import uuid
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.core.logging.logger import get_logger

logger = get_logger(__name__)

#: How many leading/trailing characters the fuzzy match anchors on
#: (about 12 words of normalized text).
ANCHOR_CHARS = 60
#: Line-merge tolerance, in points, for word tops on the same line.
_LINE_TOLERANCE = 2.0


@dataclass(frozen=True)
class ChunkToLocate:
    chunk_id: Any
    page_number: int | None
    text: str


@dataclass(frozen=True)
class ChunkLocation:
    chunk_id: Any
    page_start: int | None
    page_end: int | None
    spans: list[dict[str, Any]]
    match_quality: str  # "exact" | "fuzzy" | "none"


def _normalize(token: str) -> str:
    folded = unicodedata.normalize("NFKC", token).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def char_stream(raw_tokens: list[str]) -> tuple[str, list[int]]:
    """The tokens' letters and digits as one string, and for each
    character the index of the raw token it came from.

    Whitespace and punctuation are dropped entirely, so matching is
    immune to how either library split words: PDFs without real space
    characters come back from pdfplumber as glued words
    ("basedoncomplex...") while pypdf splits them, and a hyphenated
    line break ("learn-" / "ing") is just "learning" on both sides.
    """
    chars: list[str] = []
    owners: list[int] = []
    for index, raw in enumerate(raw_tokens):
        normalized = _normalize(raw)
        chars.append(normalized)
        owners.extend([index] * len(normalized))
    return "".join(chars), owners


def match_stream(page: str, chunk: str) -> tuple[int, int, str] | None:
    """(first, last) page-character indices the chunk covers, and quality.

    `exact`: the whole chunk occurs. `fuzzy`: its first and last
    `ANCHOR_CHARS` characters occur in order, enclosing roughly the
    chunk's own length (tolerates a differing middle, e.g. a formula
    one library reads differently).
    """
    if not chunk or not page:
        return None
    exact = page.find(chunk)
    if exact != -1:
        return exact, exact + len(chunk) - 1, "exact"
    if len(chunk) < 2 * ANCHOR_CHARS:
        return None
    head, tail = chunk[:ANCHOR_CHARS], chunk[-ANCHOR_CHARS:]
    start = page.find(head)
    while start != -1:
        tail_at = page.find(tail, start + ANCHOR_CHARS)
        if tail_at == -1:
            return None
        last = tail_at + ANCHOR_CHARS - 1
        if last - start + 1 <= int(1.5 * len(chunk)):
            return start, last, "fuzzy"
        start = page.find(head, start + 1)
    return None


def merge_line_rects(words: list[dict[str, Any]]) -> list[list[float]]:
    """Merge consecutive words on the same line into one rectangle."""
    rects: list[list[float]] = []
    for word in words:
        box = [float(word["x0"]), float(word["top"]), float(word["x1"]), float(word["bottom"])]
        if rects:
            last = rects[-1]
            same_line = abs(box[1] - last[1]) <= _LINE_TOLERANCE
            continues = box[0] >= last[0] - _LINE_TOLERANCE
            if same_line and continues:
                last[2] = max(last[2], box[2])
                last[1] = min(last[1], box[1])
                last[3] = max(last[3], box[3])
                continue
        rects.append(box)
    return [[round(value, 2) for value in rect] for rect in rects]


def locate_chunks(pdf_bytes: bytes, chunks: list[ChunkToLocate]) -> list[ChunkLocation]:
    """Locate every chunk. CPU-bound and synchronous; call it in a thread.

    Never raises for a bad chunk or page: anything that cannot be
    located is returned with `match_quality="none"`.
    """
    import pdfplumber

    by_page: dict[int, list[ChunkToLocate]] = {}
    results: list[ChunkLocation] = []
    for chunk in chunks:
        if chunk.page_number is None:
            results.append(ChunkLocation(chunk.chunk_id, None, None, [], "none"))
        else:
            by_page.setdefault(chunk.page_number, []).append(chunk)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_number, page_chunks in by_page.items():
            try:
                page = pdf.pages[page_number - 1]
                # See the module docstring: rotated pages are unsupported.
                words = (
                    []
                    if (page.rotation or 0) % 360
                    else page.extract_words(use_text_flow=True, keep_blank_chars=False)
                )
                page_chars, page_owners = char_stream([word["text"] for word in words])
                width, height = float(page.width), float(page.height)
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the rest
                logger.warning(
                    "chunk_positions_page_failed", page=page_number, error_type=type(exc).__name__
                )
                page_chars, page_owners, words = "", [], []
                width = height = 0.0

            for chunk in page_chunks:
                chunk_chars, _ = char_stream(chunk.text.split())
                found = match_stream(page_chars, chunk_chars)
                if found is None:
                    results.append(
                        ChunkLocation(chunk.chunk_id, page_number, page_number, [], "none")
                    )
                    continue
                first, last, quality = found
                word_indices = sorted(set(page_owners[first : last + 1]))
                rects = merge_line_rects([words[i] for i in word_indices])
                spans = [
                    {"page": page_number, "page_width": width, "page_height": height, "rects": rects}
                ]
                results.append(ChunkLocation(chunk.chunk_id, page_number, page_number, spans, quality))
    return results


async def store_chunk_positions(asset_id: uuid.UUID, pdf_bytes: bytes, chunks: list[Any]) -> None:
    """Locate `chunks` (committed `KnowledgeChunk` rows) and store one
    `chunk_positions` row each, on a session of its own so a failure
    here never touches the ingestion pipeline's transaction.

    If locating fails outright (a PDF pdfplumber cannot open), every
    chunk is still stored with `match_quality="none"`, so a client can
    fall back to the page.
    """
    from app.database.session import open_session
    from app.modules.knowledge_base.models import ChunkPosition

    to_locate = [ChunkToLocate(c.id, c.page_number, c.content) for c in chunks]
    try:
        locations = await asyncio.to_thread(locate_chunks, pdf_bytes, to_locate)
    except Exception as exc:  # noqa: BLE001 - recorded as unlocated
        logger.warning(
            "chunk_positions_locate_failed", asset_id=str(asset_id), error_type=type(exc).__name__
        )
        locations = [
            ChunkLocation(c.chunk_id, c.page_number, c.page_number, [], "none") for c in to_locate
        ]

    async with open_session() as session:
        session.add_all(
            ChunkPosition(
                chunk_id=location.chunk_id,
                document_id=asset_id,
                page_start=location.page_start,
                page_end=location.page_end,
                spans=location.spans,
                match_quality=location.match_quality,
            )
            for location in locations
        )
        await session.commit()
    logger.info(
        "chunk_positions_stored",
        asset_id=str(asset_id),
        exact=sum(1 for loc in locations if loc.match_quality == "exact"),
        fuzzy=sum(1 for loc in locations if loc.match_quality == "fuzzy"),
        none=sum(1 for loc in locations if loc.match_quality == "none"),
    )
