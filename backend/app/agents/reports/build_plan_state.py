"""Shared state contract for the build-plan LangGraph workflow
(Milestone 10 step 5 -- spec section 6).

Mirrors `agents.reports.state`'s conventions: one JSON-serializable
`TypedDict` every node reads from and writes to, `total=False` because
each node contributes its own slice. Unlike the synopsis state, the
`sections` channel carries an `operator.add` reducer: four nodes each
append their own finished sections to one ordered list rather than
every node re-emitting the whole list (design ruling R3).
"""

import enum
import operator
from typing import Annotated, Any, TypedDict

from app.agents.reports.state import ProcessedDocument, SectionResult

#: The exact sentence every unsupported section carries. Byte-for-byte
#: the synopsis wording (`agents.reports.nodes.write_sections_node`), so
#: a reader sees one phrase across both report kinds and a test can
#: assert on one constant (design ruling R6).
UNCOVERED = "Not covered by your documents."


class BuildPlanNode(str, enum.Enum):
    """Canonical node names for the build-plan graph."""

    EXTRACT = "extract"
    RESEARCH_FURTHER = "research_further"
    RECOMMEND_TOOLS = "recommend_tools"
    RECOMMEND_PROCESS = "recommend_process"


#: The document's sections, in render order. Not a per-kind mapping like
#: `state.SECTION_TITLES`: a build plan has exactly one shape.
BUILD_PLAN_SECTION_TITLES: list[str] = [
    "What the Paper Builds",
    "Follow-Up Research",
    "Implementations and Resources",
    "Recommended Tools",
    "Build Process",
]

#: The knowledge-base queries `extract_node` retrieves against, one per
#: field it must fill. Retrieval is per-field so a paper's datasets and
#: its compute needs are not competing for the same five excerpts.
EXTRACT_QUERIES: dict[str, str] = {
    "method": "the method, algorithm, or architecture this work proposes",
    "models": "the models, networks, or architectures used",
    "datasets": "the datasets used for training and evaluation",
    "metrics": "the evaluation metrics and how results are measured",
    "compute": "the hardware, GPUs, training time, or compute requirements",
    "limitations": "the limitations, weaknesses, and future work stated by the authors",
}

#: The build stages tools are grouped under (spec section 6), in order.
TOOL_STAGES: list[str] = ["data", "modelling", "backend", "evaluation", "deployment"]


class PaperFindings(TypedDict):
    """What `extract_node` read out of the selected papers.

    Every field is a list of short statements rather than prose, so
    `recommend_tools`/`recommend_process` can cite one specific finding
    as a tool's reason instead of quoting a paragraph.
    """

    method: list[str]
    models: list[str]
    datasets: list[str]
    metrics: list[str]
    compute: list[str]
    limitations: list[str]


class ResearchLink(TypedDict):
    """One real link from a provider result.

    The ONLY source of any URL in the finished document (design ruling
    R5): `origin` records which provider produced it, so a reviewer can
    tell an OpenAlex work from a Tavily page at a glance.
    """

    #: "openalex" or "tavily".
    origin: str
    title: str
    url: str
    note: str


class ToolRecommendation(TypedDict):
    """One recommended tool, tied to a finding from the paper."""

    stage: str
    name: str
    #: Why this tool, referencing something `extract_node` found.
    reason: str
    alternatives: list[str]


class BuildPhase(TypedDict):
    """One phase on the road from the paper's baseline to a product."""

    name: str
    steps: list[str]
    definition_of_done: str
    risks: list[str]


class BuildPlanState(TypedDict, total=False):
    """The build-plan graph's single shared state channel."""

    # --- Inputs, set by the caller before the graph starts ---
    report_id: str
    project_id: str
    owner_id: str
    #: The asset ids the builder picked, as strings.
    asset_ids: list[str]

    # --- extract output ---
    documents: list[ProcessedDocument]
    findings: PaperFindings

    # --- research_further output ---
    links: list[ResearchLink]

    # --- recommend_tools / recommend_process output ---
    tools: list[ToolRecommendation]
    phases: list[BuildPhase]

    # --- Every node appends its finished sections here (R3) ---
    sections: Annotated[list[SectionResult], operator.add]

    # --- Cross-cutting observability, the same contract
    # --- `tracking.instrument` expects. `intermediate_results` exists
    # --- because `instrument`'s non-critical failure path writes a
    # --- degraded-mode marker there; without the channel LangGraph
    # --- would reject that update.
    step: dict[str, Any]
    intermediate_results: dict[str, Any]
