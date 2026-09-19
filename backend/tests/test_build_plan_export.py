"""The build plan renders to DOCX and PDF through the step-4 export
path, with one real heading per section.

Both files are re-opened after rendering and their headings asserted --
the spec's testing requirement (section 8: "DOCX/PDF export (re-open
the file and assert headings)"). No new export code exists for the
build plan; this proves the existing renderers already carry it.
"""

from io import BytesIO

from docx import Document
from pypdf import PdfReader

from app.agents.reports.build_plan_state import BUILD_PLAN_SECTION_TITLES, UNCOVERED
from app.modules.reports.export import render_docx, render_pdf

SECTIONS = [
    {
        "title": "What the Paper Builds",
        "content": "Models:\n  - ResNet-50\nDatasets:\n  - A private poultry dataset.",
        "covered": True,
        "citations": ["asset-1"],
    },
    {
        "title": "Follow-Up Research",
        "content": "A Better Classifier (A. Author, 2025)\n  https://openalex.example/W1",
        "covered": True,
        "citations": [],
    },
    {
        "title": "Implementations and Resources",
        "content": UNCOVERED,
        "covered": False,
        "citations": [],
    },
    {
        "title": "Recommended Tools",
        "content": "Modelling\n  PyTorch\n    Why: ResNet-50 weights ship with torchvision.",
        "covered": True,
        "citations": [],
    },
    {
        "title": "Build Process",
        "content": "Phase 1: Reproduce the paper's baseline\n  Definition of done: within 2 points.",
        "covered": True,
        "citations": [],
    },
]


def test_docx_carries_a_real_heading_for_every_build_plan_section():
    content = render_docx("Build Plan", SECTIONS)

    document = Document(BytesIO(content))
    headings = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.style.name.startswith("Heading")
    ]
    assert headings == ["Build Plan", *BUILD_PLAN_SECTION_TITLES]


def test_pdf_contains_every_build_plan_section_heading_and_its_links():
    content = render_pdf("Build Plan", SECTIONS)

    text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    for title in BUILD_PLAN_SECTION_TITLES:
        assert title in text
    # The tool group, a phase, and the one real link all survive rendering.
    assert "PyTorch" in text
    assert "Phase 1" in text
    assert "openalex.example/W1" in text
    assert UNCOVERED in text
