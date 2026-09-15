"""Shared state contract for the report-generation LangGraph workflow
(Milestone 10 step 4 -- spec section 4/5).

Mirrors `agents.planner.state`'s shape and conventions: one
JSON-serializable `TypedDict` every node reads from and writes to,
`total=False` because each node contributes its own slice, no reducers
needed since this graph is strictly linear (no fan-out, no loop-back --
unlike the research graph's web-fallback loop).
"""

import enum
from typing import Any, TypedDict


class ReportNode(str, enum.Enum):
    """Canonical node names for the report graph."""

    COLLECT_DOCUMENTS = "collect_documents"
    RETRIEVE_EVIDENCE = "retrieve_evidence"
    WRITE_SECTIONS = "write_sections"
    COVERAGE_CHECK = "coverage_check"


#: The section list for each `ReportKind` (spec section 5), in the
#: order they render. "References" is special-cased in
#: `nodes.coverage_check_node` -- never sent through retrieval or the
#: LLM, always built deterministically from what the other sections
#: actually cited (spec section 7: "References are built ONLY from the
#: uploaded documents. Never invent citations.").
SECTION_TITLES: dict[str, list[str]] = {
    "study_summary": ["Key Themes", "Main Findings", "How the Documents Relate"],
    "project_synopsis": [
        "Title",
        "Abstract",
        "Introduction",
        "Problem Statement",
        "Objectives",
        "Literature Review",
        "Methodology",
        "Expected Outcomes",
        "References",
    ],
}

#: The knowledge-base search query used to retrieve evidence for one
#: section. A section not listed here (there is none today, but a
#: future section addition that forgets to register one) falls back to
#: its own title as the query in `nodes.write_sections_node`.
SECTION_QUERIES: dict[str, str] = {
    "Title": "the subject and title of this research",
    "Abstract": "a concise summary of the research",
    "Introduction": "introduction and background of the research",
    "Problem Statement": "the problem this research addresses",
    "Objectives": "the research objectives and goals",
    "Literature Review": "related work and prior research",
    "Methodology": "the methodology and methods used",
    "Expected Outcomes": "the expected outcomes and results",
    "Key Themes": "the key themes and topics across these documents",
    "Main Findings": "the main findings and results of each document",
    "How the Documents Relate": "how these documents relate to or build on each other",
}


class ProcessedDocument(TypedDict):
    """One project document available to draw a report from."""

    asset_id: str
    title: str
    file_name: str
    summary: str
    topics: list[str]


class SectionEvidence(TypedDict):
    """One retrieved excerpt offered as evidence for one section."""

    asset_id: str
    title: str
    file_name: str
    snippet: str


class SectionResult(TypedDict):
    """One finished section, ready to store on `Asset.asset_metadata`."""

    title: str
    content: str
    covered: bool
    #: Asset ids this section actually cited -- always a subset of the
    #: evidence it was shown (`nodes.write_sections_node` filters out
    #: anything else the model names).
    citations: list[str]


class ReportState(TypedDict, total=False):
    """The graph's single shared state channel."""

    # --- Inputs, set by the caller before the graph starts ---
    report_id: str
    project_id: str
    owner_id: str
    #: A `schemas.ReportKind` value.
    kind: str

    # --- collect_documents output ---
    documents: list[ProcessedDocument]

    # --- retrieve_evidence output: excerpts per section title (never
    # --- keyed for "References", which is built, not retrieved) ---
    evidence: dict[str, list[SectionEvidence]]

    # --- write_sections output (refined in place by coverage_check) ---
    sections: list[SectionResult]

    # --- Cross-cutting observability, same contract `tracking.instrument`
    # --- already expects from the research graph's `ResearchState`. ---
    step: dict[str, Any]
