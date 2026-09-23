"""Chunk -> page position mapping (click-to-source, Phase 1b).

Fixture PDFs are generated with reportlab, and chunk text comes from the
real extractor + chunker, so these tests exercise the same pypdf-vs-
pdfplumber text differences production sees.
"""

import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.modules.assets.processing.chunker import chunk_document
from app.modules.assets.processing.extractors import PdfExtractor
from app.modules.assets.processing.positions import (
    ChunkToLocate,
    char_stream,
    locate_chunks,
    match_stream,
    merge_line_rects,
)

LINES_A = [
    "Transformer models process tokens in parallel using attention.",
    "Each layer combines multi head attention with a feed forward block.",
    "Positional encodings give the model a sense of word order.",
    "Training uses large corpora and a masked language objective.",
]
LINES_B = [
    "Convolutional networks exploit local spatial structure in images.",
    "Pooling layers reduce resolution while keeping salient features.",
    "Residual connections make very deep networks trainable in practice.",
    "Batch normalization stabilizes the distribution of activations.",
]


def _pdf(draw, *, rotate: int = 0) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    if rotate:
        pdf.setPageRotation(rotate)
    pdf.setFont("Helvetica", 10)
    draw(pdf)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _lines(pdf, lines, x, y=720):
    for offset, line in enumerate(lines):
        pdf.drawString(x, y - offset * 14, line)


async def _chunks(pdf_bytes: bytes, *, chunk_size: int = 1000) -> list[ChunkToLocate]:
    extracted = await PdfExtractor().extract(pdf_bytes)
    pieces = chunk_document(extracted, chunk_size=chunk_size, chunk_overlap=20)
    return [ChunkToLocate(i, piece.page_number, piece.text) for i, piece in enumerate(pieces)]


@pytest.mark.asyncio
async def test_single_column_chunk_is_an_exact_match_with_line_boxes():
    pdf_bytes = _pdf(lambda pdf: _lines(pdf, LINES_A, 72))
    [location] = locate_chunks(pdf_bytes, await _chunks(pdf_bytes))

    assert location.match_quality == "exact"
    assert location.page_start == location.page_end == 1
    [span] = location.spans
    assert span["page_width"] == pytest.approx(612)
    assert len(span["rects"]) == len(LINES_A)  # one merged box per line
    x0, top, x1, bottom = span["rects"][0]
    # Top-left origin: the first line (drawn at y=720 from the bottom)
    # sits ~72pt below the top edge.
    assert x0 == pytest.approx(72, abs=1)
    assert 60 < top < bottom < 85
    assert x1 > x0


@pytest.mark.asyncio
async def test_two_column_page_keeps_each_chunk_in_its_own_column():
    def draw(pdf):
        _lines(pdf, LINES_A, 50)
        _lines(pdf, LINES_B, 330)

    pdf_bytes = _pdf(draw)
    column_a = ChunkToLocate("a", 1, " ".join(LINES_A))
    column_b = ChunkToLocate("b", 1, " ".join(LINES_B))
    located = {loc.chunk_id: loc for loc in locate_chunks(pdf_bytes, [column_a, column_b])}

    for chunk_id, left_edge in (("a", 50), ("b", 330)):
        location = located[chunk_id]
        assert location.match_quality in {"exact", "fuzzy"}
        rects = location.spans[0]["rects"]
        assert len(rects) == 4
        assert all(rect[0] == pytest.approx(left_edge, abs=1) for rect in rects)


@pytest.mark.asyncio
async def test_hyphenated_line_break_still_matches():
    lines = [
        "The optimizer adjusts parameters using a carefully tuned learn-",
        "ing rate schedule that decays over the course of training.",
    ]
    pdf_bytes = _pdf(lambda pdf: _lines(pdf, lines, 72))
    chunk = ChunkToLocate(
        1, 1, "The optimizer adjusts parameters using a carefully tuned learning rate "
        "schedule that decays over the course of training."
    )
    [location] = locate_chunks(pdf_bytes, [chunk])
    assert location.match_quality == "exact"
    assert len(location.spans[0]["rects"]) == 2


@pytest.mark.asyncio
async def test_rotated_page_is_a_documented_page_level_fallback():
    """Known limitation (see `positions` docstring): a rotated page is
    never highlighted from unreliable coordinates -- it is `none`, and
    the viewer opens the page."""
    pdf_bytes = _pdf(lambda pdf: _lines(pdf, LINES_A, 72), rotate=90)
    [location] = locate_chunks(pdf_bytes, await _chunks(pdf_bytes))
    assert location.match_quality == "none"
    assert location.page_start == 1
    assert location.spans == []


def test_chunk_starting_and_ending_mid_word_still_matches():
    """The chunker's overlap cuts mid-word, so chunk edges are fragments."""
    pdf_bytes = _pdf(lambda pdf: _lines(pdf, LINES_A, 72))
    text = " ".join(LINES_A)[3:-4]  # "nsformer models ... language objec"
    [location] = locate_chunks(pdf_bytes, [ChunkToLocate(1, 1, text)])
    assert location.match_quality == "exact"
    assert len(location.spans[0]["rects"]) == 4


def test_text_that_is_not_on_the_page_is_none_not_a_guess():
    pdf_bytes = _pdf(lambda pdf: _lines(pdf, LINES_A, 72))
    [location] = locate_chunks(pdf_bytes, [ChunkToLocate(1, 1, "Entirely unrelated words here.")])
    assert location.match_quality == "none"
    assert location.spans == []


def test_fuzzy_match_tolerates_a_differing_middle():
    words = [f"word{i}" for i in range(60)]
    page, _ = char_stream(words)
    chunk, _ = char_stream([*words[:30], "FORMULA", *words[30:]])
    assert match_stream(page, chunk) == (0, len(page) - 1, "fuzzy")
    # Anchors that are far too far apart are not a match.
    assert match_stream(page + "x" * 1000 + page, chunk[:70] + "zz" + chunk[-70:]) is None


def test_char_stream_ignores_word_splitting_ligatures_and_hyphenation():
    # pdfplumber on a PDF without space characters glues words; pypdf splits them.
    glued, owners = char_stream(["Eﬃcient,", "hyper-", "parameter", "basedoncomplex"])
    split, _ = char_stream(["efficient", "hyperparameter", "based", "on", "complex"])
    assert glued == split == "efficienthyperparameterbasedoncomplex"
    assert owners[0] == 0 and owners[9] == 1 and owners[-1] == 3


def test_merge_line_rects_splits_lines_and_columns():
    words = [
        {"x0": 10, "top": 100, "x1": 40, "bottom": 110},
        {"x0": 45, "top": 100, "x1": 80, "bottom": 110},
        {"x0": 10, "top": 114, "x1": 50, "bottom": 124},
    ]
    assert merge_line_rects(words) == [[10, 100, 80, 110], [10, 114, 50, 124]]
