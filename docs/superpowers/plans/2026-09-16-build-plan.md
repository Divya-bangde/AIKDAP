# Build Plan (Milestone 10, step 5) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project builder selects papers they want to turn into a real project; the platform extracts what has to be built, researches further, and returns a downloadable build plan of recommended tools and a phased process.

**Architecture:** A second linear LangGraph graph inside the existing `agents/reports/` package (`extract -> research_further -> recommend_tools -> recommend_process`), driven by the same `instrument` wrapper, the same `ReportStepTracker`, the same `generate_report` Celery task, the same idempotent `pending -> running` claim, the same retry endpoint, and the same on-demand DOCX/PDF export. Every node emits `SectionResult` entries into one `sections` channel via an `operator.add` reducer, so `asset_metadata["sections"]` keeps the exact shape `export.render_docx` / `render_pdf` already consume — no new export path.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, LangGraph, Celery, httpx, python-docx, reportlab; React + TypeScript + Vite + TanStack Query + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-13-persona-features-design.md` — section 6 "Build plan", plus its rows in section 7 (error handling) and section 8 (testing).

## Global Constraints

- Scope is **step 5 only**. Converting the build process into `tasks`-module items is explicitly out of scope (spec section "Out of scope").
- Reuse the step-4 reports base. Do **not** duplicate: asset storage, DOCX/PDF export, step tracing, the idempotency guard, or the retry endpoint.
- Endpoint: `POST /api/v1/projects/{project_id}/reports/build-plan`, body `{"asset_ids": [...]}`, returns `202` with `{asset_id, status}` — identical response model to the synopsis endpoint (`ReportGenerationAccepted`).
- Asset type `REPORT`, source `GENERATED`.
- `404` if the project is not the caller's, or if any `asset_id` is not in that project. `422` if the project has no processed documents, or if `asset_ids` is empty or names only unprocessed documents.
- **Every link shown comes from a provider result, never invented by the model.**
- A section with nothing to support it contains exactly `Not covered by your documents.` — byte-for-byte the wording the synopsis uses (`agents/reports/nodes.py`).
- A failed node leaves the asset `failed`; no partial document is ever presented as complete.
- `research_further` is **non-critical**: a missing provider key or a provider raising must not fail the report.
- Never print, log, or persist API keys or full request URLs carrying them. Errors persisted to the DB go through `scrub_report_error` or an equivalently scrubbed string.
- Frontend: the "Get build plan" button renders only when `project.effective_persona === "builder"`.
- Commits: one plan task per commit; every message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Backend commands run in Docker: `docker exec aikdap_backend <cmd>`; in Git Bash prefix container paths with `MSYS_NO_PATHCONV=1`. After changing Celery task code: `docker compose restart worker`.
- Known-environmental failures to ignore: `test_execution_*`, `test_cross_paper`, `test_fallback_grounding`.
- Skip `graphify update`.

---

## Design Rulings

These are decisions this plan locks in, called out so a reviewer can reject one without re-reading every task.

**R1 — A build plan is not a `ReportKind`.** `schemas.ReportKind` already documents itself as synopsis-only ("a build plan is not a `ReportKind`, it is its own endpoint"). The build plan gets its own request schema (`BuildPlanRequest`) and its own literal kind string `"build_plan"` stored in `asset_metadata["kind"]`. `workers.tasks._generate_report` dispatches on that string to pick the graph, the dependencies, and the node registry.

**R2 — `ReportStepTracker` takes its registry as an argument.** Today it closes over the module-level `REPORT_AGENT_REGISTRY` for `get_node_spec` (titles) and `record_skipped`. It gains a `registry` keyword argument defaulting to the synopsis registry, so the build-plan run traces its own four nodes. No behaviour change for synopsis.

**R3 — Sections accumulate through an `operator.add` reducer.** Each node returns only the sections it produced. The graph is linear, so there is no interleaving; the reducer exists purely so four nodes can contribute to one ordered list without each re-emitting the whole list.

**R4 — `research_further` records per-provider outcome and always emits its sections.** Node-level `FAILED` via `instrument` discards the node's state update entirely, which would delete the two research sections from the document and contradict "the report still completes using what is available". So the node catches per provider, always emits both sections (with the uncovered wording when a provider gave nothing), and records each provider as `ok` / `skipped: no API key configured` / `failed (<ExceptionType>)` in the step's `summary` and `output_payload`. It stays `critical=False` in the registry as a backstop for an unforeseen raise. **This is the one place this plan reads the brief's "that step is recorded as failed or skipped" as per-provider rather than per-node — reject R4 if you want the whole node marked failed and the two sections dropped.**

**R5 — Links are appended deterministically, never generated.** The tool and process prompts forbid URLs outright, and `_strip_urls` removes any the model emits anyway. Links reach the document only from a `ResearchLink` collected by `research_further` and rendered by the node itself. "Every link comes from a provider result" is therefore true by construction, not by review.

**R6 — The uncovered wording applies to provider-less sections too.** A "Follow-Up Research" section with no OpenAlex key reads `Not covered by your documents.` The brief mandates that exact string for any unsupported section; this plan does not invent a second wording for the provider case.

**R7 — The frontend extracts a shared `ReportRunPanel`.** The synopsis dialog's progress badge, timeout notice, failure + retry block, attempt-grouped pipeline, and download buttons move verbatim into `features/reports/ReportRunPanel.tsx`, consumed by both dialogs. This is reuse of the existing implementation, not a second one.

---

## File Structure

**Backend — create:**

| File | Responsibility |
|---|---|
| `backend/app/agents/reports/build_plan_state.py` | `BuildPlanNode` enum, section titles, the TypedDicts (`PaperFindings`, `ResearchLink`, `ToolRecommendation`, `BuildPhase`), and `BuildPlanState` |
| `backend/app/agents/reports/build_plan_prompts.py` | The four prompt renderers and their system prompts |
| `backend/app/agents/reports/build_plan_nodes.py` | The four nodes, the dependency bundle, link collection, link stripping |
| `backend/app/agents/reports/build_plan_registry.py` | `BUILD_PLAN_AGENT_REGISTRY` |
| `backend/app/agents/reports/build_plan_graph.py` | The compiled linear graph |
| `backend/tests/test_build_plan_nodes.py` | Node behaviour against a fake gateway and fake providers |
| `backend/tests/test_build_plan_research.py` | `research_further` degradation via `httpx.MockTransport` |
| `backend/tests/test_build_plan_routes.py` | Route-level ownership/422 through `httpx.ASGITransport` |
| `backend/tests/test_build_plan_export.py` | DOCX/PDF re-opened, headings asserted |

**Backend — modify:**

| File | Change |
|---|---|
| `backend/app/modules/reports/schemas.py` | Add `BuildPlanRequest` |
| `backend/app/modules/reports/repository.py` | Add `list_processed_documents_by_ids` and `list_project_asset_ids` |
| `backend/app/modules/reports/service.py` | Add `generate_build_plan` + `AssetSelectionError` |
| `backend/app/modules/reports/router.py` | Add the build-plan route |
| `backend/app/modules/reports/tracking.py` | `registry` keyword argument (R2) |
| `backend/app/workers/tasks.py` | Dispatch on `asset_metadata["kind"]` |

**Frontend — create:** `src/features/reports/ReportRunPanel.tsx`, `src/features/reports/BuildPlanDialog.tsx`, `src/features/reports/BuildPlanDialog.test.tsx`.

**Frontend — modify:** `src/services/reports.ts`, `src/features/reports/GenerateSynopsisDialog.tsx`, `src/features/projects/ProjectHeader.tsx`, `src/features/projects/ProjectHeader.test.tsx`, `src/types/api.ts` (regenerated).

---

## Task 1: Build-plan state, registry, and graph skeleton

**Files:**
- Create: `backend/app/agents/reports/build_plan_state.py`
- Create: `backend/app/agents/reports/build_plan_registry.py`
- Create: `backend/app/agents/reports/build_plan_graph.py`
- Create: `backend/app/agents/reports/build_plan_nodes.py` (node stubs only; Tasks 2–5 fill them)
- Modify: `backend/app/modules/reports/tracking.py`
- Test: `backend/tests/test_build_plan_nodes.py`

**Interfaces:**
- Consumes: `app.agents.planner.registry.NodeSpec`, `app.agents.planner.tracking.instrument`, `app.agents.reports.state.SectionResult`, `ProcessedDocument`, `SectionEvidence`.
- Produces: `BuildPlanNode`, `BUILD_PLAN_SECTION_TITLES`, `UNCOVERED`, `PaperFindings`, `ResearchLink`, `ToolRecommendation`, `BuildPhase`, `BuildPlanState`, `BUILD_PLAN_AGENT_REGISTRY`, `get_build_plan_graph()`, and `ReportStepTracker(session, asset_id, attempt=..., registry=...)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_plan_nodes.py`:

```python
"""Unit tests for the build-plan graph's nodes (Milestone 10 step 5).

Every node runs against fakes -- a fake LLM gateway (the `FakeGateway`
shape `tests/test_paper_suggestion.py` established), fake providers, and
a fake section searcher. No live model call, no network, no database.
"""

import json

import pytest

from app.agents.reports.build_plan_graph import get_build_plan_graph
from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.agents.reports.build_plan_state import BUILD_PLAN_SECTION_TITLES, BuildPlanNode


def test_registry_declares_the_four_nodes_with_research_non_critical():
    assert list(BUILD_PLAN_AGENT_REGISTRY) == [
        BuildPlanNode.EXTRACT.value,
        BuildPlanNode.RESEARCH_FURTHER.value,
        BuildPlanNode.RECOMMEND_TOOLS.value,
        BuildPlanNode.RECOMMEND_PROCESS.value,
    ]
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.EXTRACT.value].critical is True
    # Spec section 6/7: a missing or failing provider must not fail the report.
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RESEARCH_FURTHER.value].critical is False
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RECOMMEND_TOOLS.value].critical is True
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RECOMMEND_PROCESS.value].critical is True


def test_graph_compiles_with_every_registered_node():
    graph = get_build_plan_graph()
    assert set(BUILD_PLAN_AGENT_REGISTRY) <= set(graph.get_graph().nodes)


def test_section_titles_are_the_five_document_sections():
    assert BUILD_PLAN_SECTION_TITLES == [
        "What the Paper Builds",
        "Follow-Up Research",
        "Implementations and Resources",
        "Recommended Tools",
        "Build Process",
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents.reports.build_plan_graph'`.

- [ ] **Step 3: Write `build_plan_state.py`**

```python
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
```

- [ ] **Step 4: Write `build_plan_nodes.py` with the dependency bundle and four stubs**

```python
"""Graph nodes for build-plan generation, and the strategies they
execute against (Milestone 10 step 5 -- spec section 6).

Mirrors `agents.reports.nodes`'s shape: abstractions and the dependency
bundle first, then the nodes, injected through
`config["configurable"]["dependencies"]`.
"""

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner.nodes import SectionSearcherProtocolUnused  # replaced in Step 5
from app.core.llm.gateway import LLMGateway, get_llm_gateway


@dataclass(frozen=True)
class BuildPlanDependencies:
    """The strategies one build-plan run executes against."""

    llm_gateway: LLMGateway


def build_build_plan_dependencies(session: AsyncSession) -> BuildPlanDependencies:
    """Assemble the default dependency set for a database-backed run."""
    return BuildPlanDependencies(llm_gateway=get_llm_gateway())


def _dependencies(config: RunnableConfig) -> BuildPlanDependencies:
    dependencies = (config.get("configurable") or {}).get("dependencies")
    if not isinstance(dependencies, BuildPlanDependencies):
        raise RuntimeError(
            "Build-plan graph invoked without a BuildPlanDependencies instance in "
            "config['configurable']['dependencies']."
        )
    return dependencies


async def extract_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def research_further_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def recommend_tools_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def recommend_process_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError
```

Delete the placeholder `SectionSearcherProtocolUnused` import line before running — it exists in this listing only to mark where Task 2 adds the real retrieval imports. The stub file must import cleanly:

```python
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm.gateway import LLMGateway, get_llm_gateway
```

- [ ] **Step 5: Write `build_plan_registry.py`**

```python
"""Registry of the build-plan graph's nodes -- mirrors
`agents.reports.registry`, reusing `agents.planner.registry.NodeSpec`.

Unlike the synopsis registry (every node critical), `research_further`
is `critical=False`: OpenAlex and Tavily are external services, and the
spec requires the report to complete on whatever is available when one
is missing a key or fails (spec sections 6 and 7).
"""

from app.agents.planner.registry import NodeSpec
from app.agents.reports.build_plan_nodes import (
    extract_node,
    recommend_process_node,
    recommend_tools_node,
    research_further_node,
)
from app.agents.reports.build_plan_state import BuildPlanNode

BUILD_PLAN_AGENT_REGISTRY: dict[str, NodeSpec] = {
    BuildPlanNode.EXTRACT.value: NodeSpec(
        name=BuildPlanNode.EXTRACT.value,
        title="Extract what the paper builds",
        handler=extract_node,
        critical=True,
        description="Reads method, models, datasets, metrics, compute needs and limitations from the selected papers.",
    ),
    BuildPlanNode.RESEARCH_FURTHER.value: NodeSpec(
        name=BuildPlanNode.RESEARCH_FURTHER.value,
        title="Research further",
        handler=research_further_node,
        critical=False,
        description="Searches OpenAlex for follow-up methods and Tavily for implementations, libraries and datasets.",
    ),
    BuildPlanNode.RECOMMEND_TOOLS.value: NodeSpec(
        name=BuildPlanNode.RECOMMEND_TOOLS.value,
        title="Recommend tools",
        handler=recommend_tools_node,
        critical=True,
        description="Recommends tools per build stage, each with a reason from the paper and alternatives.",
    ),
    BuildPlanNode.RECOMMEND_PROCESS.value: NodeSpec(
        name=BuildPlanNode.RECOMMEND_PROCESS.value,
        title="Recommend a build process",
        handler=recommend_process_node,
        critical=True,
        description="Lays out phases from reproducing the baseline to a working product, with done criteria and risks.",
    ),
}


def get_build_plan_node_spec(name: str) -> NodeSpec:
    """Look up one build-plan node's spec by name."""
    try:
        return BUILD_PLAN_AGENT_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown build-plan node '{name}'. "
            f"Registered: {', '.join(sorted(BUILD_PLAN_AGENT_REGISTRY))}."
        ) from exc
```

- [ ] **Step 6: Write `build_plan_graph.py`**

```python
"""The build-plan LangGraph orchestrator: a strictly linear graph
(spec section 6) -- extract, research further, recommend tools,
recommend a process, done. No conditional edges and no loop-back, the
same shape `agents.reports.graph` uses for the synopsis.

Reuses `agents.planner.tracking.instrument`, so each node gets the same
timing/logging/failure-policy wrapper -- which is also what makes
`research_further`'s `critical=False` take effect without any
build-plan-specific error handling in the graph itself.
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.planner.tracking import instrument
from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.agents.reports.build_plan_state import BuildPlanNode, BuildPlanState


def build_build_plan_graph() -> StateGraph:
    """Construct the uncompiled build-plan graph from its registry."""
    builder: StateGraph = StateGraph(BuildPlanState)

    for name, spec in BUILD_PLAN_AGENT_REGISTRY.items():
        builder.add_node(name, instrument(spec))

    builder.add_edge(START, BuildPlanNode.EXTRACT.value)
    builder.add_edge(BuildPlanNode.EXTRACT.value, BuildPlanNode.RESEARCH_FURTHER.value)
    builder.add_edge(BuildPlanNode.RESEARCH_FURTHER.value, BuildPlanNode.RECOMMEND_TOOLS.value)
    builder.add_edge(BuildPlanNode.RECOMMEND_TOOLS.value, BuildPlanNode.RECOMMEND_PROCESS.value)
    builder.add_edge(BuildPlanNode.RECOMMEND_PROCESS.value, END)

    return builder


@lru_cache(maxsize=1)
def get_build_plan_graph() -> CompiledStateGraph:
    """Return the process-wide compiled build-plan graph, compiled once
    and reused -- it carries no run-specific state, the same reasoning
    `agents.reports.graph.get_report_graph` documents."""
    return build_build_plan_graph().compile()
```

- [ ] **Step 7: Make `ReportStepTracker` registry-aware (R2)**

In `backend/app/modules/reports/tracking.py`, replace the import of `get_node_spec` and the constructor, and route both `get_node_spec` call sites through the instance's registry.

Replace:

```python
from app.agents.reports.registry import REPORT_AGENT_REGISTRY, get_node_spec
```

with:

```python
from app.agents.planner.registry import NodeSpec
from app.agents.reports.registry import REPORT_AGENT_REGISTRY
```

Replace the constructor with:

```python
    def __init__(
        self,
        session: AsyncSession,
        asset_id: uuid.UUID,
        *,
        attempt: int,
        registry: dict[str, NodeSpec] | None = None,
    ) -> None:
        self._session = session
        self._asset_id = asset_id
        self._attempt = attempt
        # Which graph's nodes this run traces. Defaults to the synopsis
        # registry so every existing caller is unchanged; the build-plan
        # task passes `BUILD_PLAN_AGENT_REGISTRY` (design ruling R2).
        self._registry = registry if registry is not None else REPORT_AGENT_REGISTRY
        self._steps = ResearchStepRepository(session)
        self._step_index = 0
        # A plain UUID, not an ORM instance: the failure path's rollback
        # expires every loaded object (same reasoning as
        # `research.service.ResearchStepTracker`).
        self._current_step_id: uuid.UUID | None = None
        self.started_nodes: list[str] = []

    def _spec(self, node: str) -> NodeSpec:
        """This run's registry entry for `node`."""
        try:
            return self._registry[node]
        except KeyError as exc:
            raise KeyError(
                f"Unknown report node '{node}'. Registered: {', '.join(sorted(self._registry))}."
            ) from exc
```

Then replace the three registry references in the methods:
- in `on_node_start`: `title=get_node_spec(node).title` becomes `title=self._spec(node).title`
- in `on_node_failure`: `f"Failed: {get_node_spec(node).title}."` becomes `f"Failed: {self._spec(node).title}."`
- in `record_skipped`: `for name, spec in REPORT_AGENT_REGISTRY.items():` becomes `for name, spec in self._registry.items():`

- [ ] **Step 8: Run the tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py tests/test_report_steps.py tests/test_reports_graph.py -v`
Expected: PASS — the three new tests plus the existing step-4 tracker and graph tests unchanged.

- [ ] **Step 9: Commit**

```bash
git add backend/app/agents/reports/build_plan_state.py backend/app/agents/reports/build_plan_nodes.py backend/app/agents/reports/build_plan_registry.py backend/app/agents/reports/build_plan_graph.py backend/app/modules/reports/tracking.py backend/tests/test_build_plan_nodes.py
git commit -m "feat(reports): add the build-plan graph skeleton and a registry-aware step tracker

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: The `extract` node

**Files:**
- Modify: `backend/app/agents/reports/build_plan_nodes.py`
- Create: `backend/app/agents/reports/build_plan_prompts.py`
- Test: `backend/tests/test_build_plan_nodes.py`

**Interfaces:**
- Consumes: `BuildPlanDependencies` (Task 1), `agents.reports.nodes.DocumentLister`/`SectionSearcher`/`KnowledgeBaseSectionSearcher`/`RepositoryDocumentLister`, `EXTRACT_QUERIES`, `UNCOVERED`, `PaperFindings`.
- Produces: `extract_node` writing `documents`, `findings`, and the `"What the Paper Builds"` section; `BuildPlanDependencies(document_lister, section_searcher, llm_gateway, paper_provider, web_provider)`; `render_findings_section(findings)`; `FindingsDraft`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_build_plan_nodes.py`:

```python
from app.agents.reports.build_plan_nodes import BuildPlanDependencies, extract_node
from app.agents.reports.build_plan_state import UNCOVERED
from app.agents.reports.state import ProcessedDocument, SectionEvidence


class FakeGateway:
    """The `FakeGateway` shape `tests/test_paper_suggestion.py` uses:
    returns canned content and records every call's kwargs."""

    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        content = self._contents.pop(0) if len(self._contents) > 1 else self._contents[0]
        return LLMResponse(content=content, model="fake-model", provider="fake", latency_ms=5)


class FakeDocumentLister:
    def __init__(self, documents: list[ProcessedDocument]) -> None:
        self._documents = documents
        self.requested_ids: list[str] | None = None

    async def list_selected(self, project_id, asset_ids):
        self.requested_ids = list(asset_ids)
        return self._documents


class FakeSectionSearcher:
    """Returns one excerpt per query unless constructed empty."""

    def __init__(self, *, empty: bool = False) -> None:
        self._empty = empty
        self.queries: list[str] = []

    async def search(self, *, owner_id, project_id, query, limit):
        self.queries.append(query)
        if self._empty:
            return []
        return [
            SectionEvidence(
                asset_id="asset-1",
                title="A Paper",
                file_name="paper.pdf",
                snippet=f"Evidence about {query}.",
            )
        ]


def _document() -> ProcessedDocument:
    return ProcessedDocument(
        asset_id="asset-1",
        title="A Paper",
        file_name="paper.pdf",
        summary="Detects disease in poultry from images.",
        topics=["poultry", "vision"],
    )


FINDINGS_JSON = json.dumps(
    {
        "method": ["A fine-tuned convolutional classifier."],
        "models": ["ResNet-50"],
        "datasets": ["A private 4,000-image poultry dataset."],
        "metrics": ["Accuracy", "F1"],
        "compute": ["One NVIDIA V100 for 6 hours."],
        "limitations": ["The dataset is not public."],
    }
)


def _dependencies(gateway, lister, searcher, *, paper_provider=None, web_provider=None):
    return BuildPlanDependencies(
        document_lister=lister,
        section_searcher=searcher,
        llm_gateway=gateway,
        paper_provider=paper_provider,
        web_provider=web_provider,
    )


def _config(dependencies):
    return {"configurable": {"dependencies": dependencies}}


@pytest.mark.asyncio
async def test_extract_node_produces_its_section_from_one_structured_llm_call():
    gateway = FakeGateway(FINDINGS_JSON)
    lister = FakeDocumentLister([_document()])
    searcher = FakeSectionSearcher()
    dependencies = _dependencies(gateway, lister, searcher)

    state = {
        "report_id": "r1",
        "project_id": "p1",
        "owner_id": "u1",
        "asset_ids": ["asset-1"],
    }
    update = await extract_node(state, _config(dependencies))

    # Only the papers the builder picked are listed.
    assert lister.requested_ids == ["asset-1"]
    # Retrieval comes from the knowledge base, one query per field.
    assert len(searcher.queries) == 6
    # Exactly one structured LLM call produces every field.
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["response_format"]["type"] == "json_schema"

    assert update["findings"]["models"] == ["ResNet-50"]
    section = update["sections"][0]
    assert section["title"] == "What the Paper Builds"
    assert section["covered"] is True
    assert "ResNet-50" in section["content"]
    assert "The dataset is not public." in section["content"]
    assert section["citations"] == ["asset-1"]


@pytest.mark.asyncio
async def test_extract_node_marks_the_section_uncovered_without_calling_the_model():
    gateway = FakeGateway(FINDINGS_JSON)
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher(empty=True))

    update = await extract_node(
        {"report_id": "r1", "project_id": "p1", "owner_id": "u1", "asset_ids": ["asset-1"]},
        _config(dependencies),
    )

    assert gateway.calls == []
    section = update["sections"][0]
    assert section["content"] == UNCOVERED
    assert section["covered"] is False
    assert section["citations"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py -v`
Expected: FAIL with `NotImplementedError` (and an `ImportError`/`TypeError` on `BuildPlanDependencies` until Step 3 widens it).

- [ ] **Step 3: Write `build_plan_prompts.py`**

```python
"""Prompt templates for the build-plan graph's four LLM calls
(Milestone 10 step 5 -- spec section 6).

Every prompt carries the same two hard rules the spec sets: ground
everything in the provided material, and never write a URL. Links are
appended deterministically from provider results by the nodes
themselves (design ruling R5), so a model that invents one produces
nothing a reader can click.
"""

from app.agents.reports.build_plan_state import (
    BuildPhase,
    PaperFindings,
    ResearchLink,
    TOOL_STAGES,
    ToolRecommendation,
)
from app.agents.reports.state import ProcessedDocument, SectionEvidence

#: Repeated in every system prompt below. Stated once so the four
#: prompts cannot drift on the rule that matters most.
_NO_LINKS_RULE = (
    "Never write a URL, link, DOI, or web address of any kind -- not in "
    "prose, not in parentheses, not as a citation. Links are added "
    "separately from verified search results. Any URL you write will be "
    "deleted before the reader sees it."
)

EXTRACT_SYSTEM_PROMPT = (
    "You read a research paper the user wants to turn into a real "
    "project, and you report only what the paper itself says. Base every "
    "field strictly on the provided excerpts. If the excerpts do not "
    "state something, return an empty list for that field rather than "
    "guessing a plausible value. " + _NO_LINKS_RULE
)

TOOLS_SYSTEM_PROMPT = (
    "You recommend concrete, well-known tools and libraries for building "
    "the described system. Every recommendation must name a real tool and "
    "give a reason that refers to something specific in the paper's "
    "findings -- a named model, dataset, metric, or compute need. Offer "
    "alternatives so the builder is not locked in. " + _NO_LINKS_RULE
)

PROCESS_SYSTEM_PROMPT = (
    "You lay out the phases of building a working product from a research "
    "paper, starting from reproducing the paper's own baseline. Each phase "
    "gets concrete steps, one checkable definition of done, and the risks "
    "that phase actually carries -- for example a dataset that needs "
    "licence approval before it can be used. " + _NO_LINKS_RULE
)


def render_extract_prompt(
    *, documents: list[ProcessedDocument], evidence: dict[str, list[SectionEvidence]]
) -> str:
    """One prompt for one structured call that fills every field.

    Field-scoped excerpts are labelled with the field they were
    retrieved for, so the model sees which evidence answers which
    question instead of one undifferentiated pile.
    """
    document_lines = "\n".join(
        f"- [{document['asset_id']}] {document['title']}: "
        f"{document['summary'] or 'No summary available.'}"
        for document in documents
    )
    evidence_blocks = []
    for field, items in evidence.items():
        if not items:
            continue
        excerpts = "\n".join(f"  - [{item['asset_id']}] {item['snippet']}" for item in items)
        evidence_blocks.append(f"Excerpts retrieved for '{field}':\n{excerpts}")
    evidence_block = "\n\n".join(evidence_blocks) or "No excerpts available."

    return (
        f"Selected papers:\n{document_lines}\n\n"
        f"{evidence_block}\n\n"
        "From these papers only, report what would have to be built. "
        "Return JSON with these keys, each a list of short statements: "
        "'method' (the method or algorithm proposed), 'models' (the models "
        "or architectures used), 'datasets' (the datasets used), 'metrics' "
        "(the evaluation metrics), 'compute' (hardware, GPU, or training-time "
        "needs), and 'limitations' (the limitations the authors themselves "
        "state). Use an empty list for anything the excerpts do not state."
    )


def render_tools_prompt(*, findings: PaperFindings, links: list[ResearchLink]) -> str:
    """Prompt for the per-stage tool recommendations.

    `links` are passed as titles and notes only -- never URLs -- so the
    model can take a real implementation or library into account without
    ever being handed a URL it might echo back.
    """
    findings_block = _findings_block(findings)
    resources = (
        "\n".join(f"- {link['title']}: {link['note']}" for link in links)
        or "No external search results were available."
    )
    stages = ", ".join(TOOL_STAGES)
    return (
        f"Findings from the paper:\n{findings_block}\n\n"
        f"Resources found by search (titles only):\n{resources}\n\n"
        f"Recommend tools for building this system, grouped by stage. "
        f"Use exactly these stage values: {stages}. Return JSON with a "
        "'tools' list; each item has 'stage' (one of the stage values), "
        "'name' (the tool), 'reason' (why, referring to a specific "
        "finding above), and 'alternatives' (a list of other tools that "
        "would also work). Cover every stage that the findings support."
    )


def render_process_prompt(*, findings: PaperFindings, tools: list[ToolRecommendation]) -> str:
    """Prompt for the phased build process."""
    findings_block = _findings_block(findings)
    tool_lines = (
        "\n".join(f"- {tool['stage']}: {tool['name']} ({tool['reason']})" for tool in tools)
        or "No tools were recommended."
    )
    return (
        f"Findings from the paper:\n{findings_block}\n\n"
        f"Recommended tools:\n{tool_lines}\n\n"
        "Lay out the phases for going from this paper to a working "
        "product. The first phase must be reproducing the paper's own "
        "baseline result; the last must be a deployed, usable product. "
        "Return JSON with a 'phases' list; each item has 'name', 'steps' "
        "(a list of concrete actions), 'definition_of_done' (one "
        "checkable sentence), and 'risks' (a list of what could block "
        "this phase, such as a dataset needing licence approval)."
    )


def _findings_block(findings: PaperFindings) -> str:
    """Render extracted findings as labelled lines for a prompt."""
    lines = []
    for field, values in findings.items():
        rendered = "; ".join(values) if values else "not stated in the paper"
        lines.append(f"- {field}: {rendered}")
    return "\n".join(lines)
```

- [ ] **Step 4: Replace `build_plan_nodes.py` with the dependency bundle and `extract_node`**

```python
"""Graph nodes for build-plan generation, and the strategies they
execute against (Milestone 10 step 5 -- spec section 6).

Mirrors `agents.reports.nodes`'s shape: abstractions and the dependency
bundle first, then the nodes, injected through
`config["configurable"]["dependencies"]`.

Retrieval and document listing are deliberately the step-4 abstractions
(`DocumentLister`, `SectionSearcher`) rather than new ones -- a build
plan retrieves from the same knowledge base, through the same two-stage
search, as a synopsis.
"""

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner.nodes import WebResearchProvider, get_paper_provider, get_web_provider
from app.agents.planner.paper_suggestion import OpenAlexProvider
from app.agents.reports.build_plan_prompts import (
    EXTRACT_SYSTEM_PROMPT,
    PROCESS_SYSTEM_PROMPT,
    TOOLS_SYSTEM_PROMPT,
    render_extract_prompt,
    render_process_prompt,
    render_tools_prompt,
)
from app.agents.reports.build_plan_state import (
    BUILD_PLAN_SECTION_TITLES,
    BuildPhase,
    BuildPlanState,
    EXTRACT_QUERIES,
    PaperFindings,
    ResearchLink,
    TOOL_STAGES,
    ToolRecommendation,
    UNCOVERED,
)
from app.agents.reports.nodes import (
    KnowledgeBaseSectionSearcher,
    SECTION_EVIDENCE_LIMIT,
    SectionSearcher,
    _document_from_asset,
)
from app.agents.reports.state import ProcessedDocument, SectionEvidence, SectionResult
from app.core.config.settings import settings
from app.core.llm.gateway import LLMGateway, get_llm_gateway
from app.core.logging.logger import get_logger
from app.modules.reports.repository import ReportRepository

logger = get_logger(__name__)

#: How many follow-up works and web resources to ask each provider for.
PROVIDER_RESULT_LIMIT = 5

#: Any URL-looking run of text. Applied to every piece of model output
#: before it reaches a section, so a link can only enter the document
#: through a `ResearchLink` from a provider (design ruling R5).
_URL_PATTERN = re.compile(r"(?:https?://|www\.|doi:)\S+", re.IGNORECASE)

#: Strips a ```json ... ``` fence, the same tolerance
#: `agents.reports.nodes._parse_section` applies.
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Accept a raw state string or an already-parsed UUID."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def strip_urls(text: str) -> str:
    """Remove every URL-looking token from model output.

    The enforcement half of design ruling R5: the prompts forbid URLs,
    and this guarantees a model that writes one anyway cannot put an
    unverified link in front of the reader. Provider links are appended
    by the nodes after this runs, so they are never stripped.
    """
    return _URL_PATTERN.sub("", text).replace("()", "").strip()


class FindingsDraft(BaseModel):
    """The LLM's structured response for `extract_node`."""

    method: list[str] = []
    models: list[str] = []
    datasets: list[str] = []
    metrics: list[str] = []
    compute: list[str] = []
    limitations: list[str] = []


class ToolsDraft(BaseModel):
    """The LLM's structured response for `recommend_tools_node`."""

    class Tool(BaseModel):
        stage: str
        name: str
        reason: str
        alternatives: list[str] = []

    tools: list[Tool] = []


class ProcessDraft(BaseModel):
    """The LLM's structured response for `recommend_process_node`."""

    class Phase(BaseModel):
        name: str
        steps: list[str] = []
        definition_of_done: str = ""
        risks: list[str] = []

    phases: list[Phase] = []


# ---------------------------------------------------------------------------
# Abstraction points
# ---------------------------------------------------------------------------


class SelectedDocumentLister(ABC):
    """Contract for listing the specific papers the builder picked.

    Distinct from step 4's `DocumentLister` (which lists every processed
    document in a project): a build plan runs over a chosen subset, so
    the selection is part of the contract."""

    @abstractmethod
    async def list_selected(
        self, project_id: str | uuid.UUID, asset_ids: list[str]
    ) -> list[ProcessedDocument]:
        """Return the processed documents among `asset_ids`, in project order."""


class RepositorySelectedDocumentLister(SelectedDocumentLister):
    """Lists the selected papers through `ReportRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = ReportRepository(session)

    async def list_selected(
        self, project_id: str | uuid.UUID, asset_ids: list[str]
    ) -> list[ProcessedDocument]:
        assets = await self._repository.list_processed_documents_by_ids(
            _as_uuid(project_id), [_as_uuid(asset_id) for asset_id in asset_ids]
        )
        return [_document_from_asset(asset) for asset in assets]


@dataclass(frozen=True)
class BuildPlanDependencies:
    """The strategies one build-plan run executes against, injected
    through `config["configurable"]["dependencies"]` -- the same pattern
    `agents.reports.nodes.ReportGraphDependencies` uses.

    `paper_provider` is `None` when no OpenAlex key is configured, the
    same load-bearing `None` `get_paper_provider` returns for the
    research graph. `web_provider` is `None` when no Tavily key is set:
    unlike the research graph, a build plan must not fall back to
    `MockWebResearchProvider`, because a simulated result has no real
    link and this feature's whole promise is real ones.
    """

    document_lister: SelectedDocumentLister
    section_searcher: SectionSearcher
    llm_gateway: LLMGateway
    paper_provider: OpenAlexProvider | None
    web_provider: WebResearchProvider | None


def build_build_plan_dependencies(session: AsyncSession) -> BuildPlanDependencies:
    """Assemble the default dependency set for a database-backed run."""
    web_provider = get_web_provider()
    return BuildPlanDependencies(
        document_lister=RepositorySelectedDocumentLister(session),
        section_searcher=KnowledgeBaseSectionSearcher(session),
        llm_gateway=get_llm_gateway(),
        paper_provider=get_paper_provider(),
        # A simulated provider is treated as no provider: the build plan
        # shows real links or says nothing (spec section 7 forbids
        # simulated results standing in for real ones).
        web_provider=web_provider if web_provider.live else None,
    )


def _dependencies(config: RunnableConfig) -> BuildPlanDependencies:
    dependencies = (config.get("configurable") or {}).get("dependencies")
    if not isinstance(dependencies, BuildPlanDependencies):
        raise RuntimeError(
            "Build-plan graph invoked without a BuildPlanDependencies instance in "
            "config['configurable']['dependencies']."
        )
    return dependencies


def _section(title: str, content: str, citations: list[str]) -> SectionResult:
    """Build one `SectionResult`, applying the uncovered rule in one place.

    Every section in this graph goes through here, so the exact
    `UNCOVERED` wording and the "uncovered sections carry no citations"
    rule cannot drift between nodes.
    """
    body = content.strip()
    if not body:
        return SectionResult(title=title, content=UNCOVERED, covered=False, citations=[])
    return SectionResult(title=title, content=body, covered=True, citations=citations)


def _parse(model: type[BaseModel], content: str, label: str) -> Any:
    """Parse a node's structured response, raising on anything unusable.

    Same policy as `agents.reports.nodes._parse_section`: a critical
    node must fail loudly rather than present an ungrounded section.
    """
    match = _JSON_FENCE.match(content)
    text = match.group(1) if match else content
    try:
        return model.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise RuntimeError(f"Could not parse the {label} response.") from exc


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def render_findings_section(findings: PaperFindings) -> str:
    """Render extracted findings as the document's first section.

    Plain labelled lines, not markdown: `export.render_docx` writes one
    paragraph per section and `render_pdf` escapes the text, so headings
    inside a section body would render as literal characters.
    """
    labels = [
        ("method", "Method or algorithm"),
        ("models", "Models"),
        ("datasets", "Datasets"),
        ("metrics", "Evaluation metrics"),
        ("compute", "Compute and hardware needs"),
        ("limitations", "Limitations stated by the authors"),
    ]
    lines = []
    for key, label in labels:
        values = findings.get(key) or []
        if not values:
            # A field the paper does not state is named as unstated
            # rather than dropped: the builder should see that the paper
            # is silent on its own compute needs.
            lines.append(f"{label}: not stated in the selected papers.")
            continue
        lines.append(f"{label}:")
        lines.extend(f"  - {value}" for value in values)
    return "\n".join(lines)


async def extract_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    """Read what has to be built out of the selected papers.

    Retrieval is per-field against the project knowledge base, then one
    structured LLM call fills every field at once (spec section 6:
    "one structured LLM call produces the fields").
    """
    dependencies = _dependencies(config)
    documents = await dependencies.document_lister.list_selected(
        state["project_id"], state.get("asset_ids", [])
    )

    evidence: dict[str, list[SectionEvidence]] = {}
    for field, query in EXTRACT_QUERIES.items():
        evidence[field] = await dependencies.section_searcher.search(
            owner_id=state["owner_id"],
            project_id=state["project_id"],
            query=query,
            limit=SECTION_EVIDENCE_LIMIT,
        )

    selected_ids = {document["asset_id"] for document in documents}
    # Only excerpts from the papers the builder actually picked may
    # ground this report -- retrieval is project-wide, the selection is not.
    evidence = {
        field: [item for item in items if item["asset_id"] in selected_ids]
        for field, items in evidence.items()
    }

    if not any(evidence.values()):
        # No evidence at all: mark the section uncovered without ever
        # calling the model, exactly as `write_sections_node` does.
        logger.info("build_plan_extract_uncovered", report_id=state.get("report_id"))
        return {
            "documents": documents,
            "findings": PaperFindings(
                method=[], models=[], datasets=[], metrics=[], compute=[], limitations=[]
            ),
            "sections": [_section(BUILD_PLAN_SECTION_TITLES[0], "", [])],
            "step": {
                "summary": "No excerpts found in the selected papers.",
                "output": {"document_count": len(documents), "llm_calls": 0},
            },
        }

    response = await dependencies.llm_gateway.generate(
        prompt=render_extract_prompt(documents=documents, evidence=evidence),
        system_prompt=EXTRACT_SYSTEM_PROMPT,
        model=settings.synthesis_model,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "FindingsDraft", "schema": FindingsDraft.model_json_schema()},
        },
    )
    draft: FindingsDraft = _parse(FindingsDraft, response.content, "paper-findings")
    findings = PaperFindings(
        method=[strip_urls(value) for value in draft.method],
        models=[strip_urls(value) for value in draft.models],
        datasets=[strip_urls(value) for value in draft.datasets],
        metrics=[strip_urls(value) for value in draft.metrics],
        compute=[strip_urls(value) for value in draft.compute],
        limitations=[strip_urls(value) for value in draft.limitations],
    )

    cited = sorted(
        {item["asset_id"] for items in evidence.values() for item in items}
    )
    field_count = sum(1 for values in findings.values() if values)
    logger.info(
        "build_plan_extracted",
        report_id=state.get("report_id"),
        document_count=len(documents),
        fields_filled=field_count,
    )
    return {
        "documents": documents,
        "findings": findings,
        "sections": [_section(BUILD_PLAN_SECTION_TITLES[0], render_findings_section(findings), cited)],
        "step": {
            "summary": f"Extracted {field_count} of 6 field(s) from {len(documents)} paper(s).",
            "output": {
                "document_count": len(documents),
                "fields_filled": field_count,
                "llm_calls": 1,
            },
        },
    }


async def research_further_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def recommend_tools_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def recommend_process_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError
```

- [ ] **Step 5: Add the repository method `extract_node` depends on**

In `backend/app/modules/reports/repository.py`, add to `ReportRepository`:

```python
    async def list_processed_documents_by_ids(
        self, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> list[Asset]:
        """The subset of `list_processed_documents` the caller selected.

        Filtered in SQL rather than in Python so a selection naming a
        thousand ids does not load the whole project. An empty
        `asset_ids` returns nothing: "select none" is never "select
        all" -- the service rejects an empty selection with a 422 before
        this is reached, and this method must not silently disagree.
        """
        if not asset_ids:
            return []
        stmt = (
            select(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.id.in_(asset_ids),
                Asset.asset_type == AssetType.DOCUMENT,
                Asset.processing_status == AssetProcessingStatus.COMPLETED,
            )
            .order_by(Asset.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_project_asset_ids(self, project_id: uuid.UUID) -> set[uuid.UUID]:
        """Every asset id in this project, whatever its type or status.

        The ownership check for a build-plan selection, and deliberately
        broader than `list_processed_documents_by_ids`: "this id is not in
        your project" (a `404`) and "this id is in your project but is not
        a processed document" (a `422`) are different answers, so the
        service needs both sets rather than inferring one from the other.

        `AssetRepository` has no project-wide listing method -- its
        `search` is a paged, filtered query for the assets API -- so this
        lives here rather than widening that one for a membership test.
        """
        result = await self._session.execute(
            select(Asset.id).where(Asset.project_id == project_id)
        )
        return set(result.scalars().all())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py -v`
Expected: PASS — the two `extract_node` tests plus Task 1's three.

- [ ] **Step 7: Commit**

```bash
git add backend/app/agents/reports/build_plan_nodes.py backend/app/agents/reports/build_plan_prompts.py backend/app/modules/reports/repository.py backend/tests/test_build_plan_nodes.py
git commit -m "feat(reports): extract method, models, datasets, metrics, compute and limitations from the selected papers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: The `research_further` node and its degradation

**Files:**
- Modify: `backend/app/agents/reports/build_plan_nodes.py`
- Test: `backend/tests/test_build_plan_nodes.py`, `backend/tests/test_build_plan_research.py`

**Interfaces:**
- Consumes: `BuildPlanDependencies` (Task 2), `OpenAlexProvider.search(query=, limit=) -> list[SuggestedPaper]`, `WebResearchProvider.search(query=, limit=) -> list[RetrievedDocument]`, `PROVIDER_RESULT_LIMIT`, `_section`, `strip_urls`.
- Produces: `research_further_node` writing `links` and the `"Follow-Up Research"` + `"Implementations and Resources"` sections; `build_research_queries(findings)`; `render_links_section(links)`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_build_plan_nodes.py`:

```python
from app.agents.reports.build_plan_nodes import research_further_node


class FakePaperProvider:
    def __init__(self, papers=None, error: Exception | None = None) -> None:
        self._papers = papers or []
        self._error = error
        self.queries: list[str] = []

    async def search(self, *, query, limit):
        self.queries.append(query)
        if self._error:
            raise self._error
        return self._papers


class FakeWebProvider:
    live = True

    def __init__(self, documents=None, error: Exception | None = None) -> None:
        self._documents = documents or []
        self._error = error
        self.queries: list[str] = []

    async def search(self, *, query, limit):
        self.queries.append(query)
        if self._error:
            raise self._error
        return self._documents


def _paper(url="https://openalex.example/W1"):
    return {
        "openalex_id": "https://openalex.org/W1",
        "title": "A Better Classifier",
        "authors": ["A. Author"],
        "year": 2025,
        "cited_by_count": 12,
        "landing_url": url,
        "oa_pdf_url": None,
        "relevance_note": "Abstract: improves on ResNet-50.",
    }


def _web(url="https://github.example/repo"):
    return {
        "source": "web",
        "provider": "tavily_web_v1",
        "reference": url,
        "title": "An open-source implementation",
        "snippet": "A PyTorch reimplementation with pretrained weights.",
        "score": 0.9,
        "simulated": False,
        "rank": 1,
    }


FINDINGS = {
    "method": ["A fine-tuned convolutional classifier."],
    "models": ["ResNet-50"],
    "datasets": ["A private 4,000-image poultry dataset."],
    "metrics": ["Accuracy"],
    "compute": ["One NVIDIA V100."],
    "limitations": ["The dataset is not public."],
}


@pytest.mark.asyncio
async def test_research_further_produces_both_sections_with_provider_links():
    papers = FakePaperProvider([_paper()])
    web = FakeWebProvider([_web()])
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=papers,
        web_provider=web,
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    titles = [section["title"] for section in update["sections"]]
    assert titles == ["Follow-Up Research", "Implementations and Resources"]
    assert all(section["covered"] for section in update["sections"])
    assert "https://openalex.example/W1" in update["sections"][0]["content"]
    assert "https://github.example/repo" in update["sections"][1]["content"]
    # Every collected link is recorded in state for the later nodes.
    assert {link["url"] for link in update["links"]} == {
        "https://openalex.example/W1",
        "https://github.example/repo",
    }
    assert update["step"]["output"]["openalex"] == "ok"
    assert update["step"]["output"]["tavily"] == "ok"


@pytest.mark.asyncio
async def test_research_further_records_a_missing_key_as_skipped_and_still_completes():
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=None,
        web_provider=None,
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    assert update["links"] == []
    assert [section["content"] for section in update["sections"]] == [UNCOVERED, UNCOVERED]
    assert update["step"]["output"]["openalex"] == "skipped: no API key configured"
    assert update["step"]["output"]["tavily"] == "skipped: no API key configured"


@pytest.mark.asyncio
async def test_research_further_records_a_raising_provider_with_a_scrubbed_error():
    secret = "sk-live-do-not-leak"
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=FakePaperProvider(error=RuntimeError(f"boom {secret}")),
        web_provider=FakeWebProvider([_web()]),
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    # The failing provider's own message never reaches the step payload.
    assert update["step"]["output"]["openalex"] == "failed (RuntimeError)"
    assert secret not in json.dumps(update["step"])
    # The other provider's results still land, and the report completes.
    assert update["sections"][0]["content"] == UNCOVERED
    assert update["sections"][1]["covered"] is True
    assert update["step"]["output"]["tavily"] == "ok"


@pytest.mark.asyncio
async def test_no_link_in_the_output_is_absent_from_the_provider_results():
    papers = FakePaperProvider([_paper("https://openalex.example/W1")])
    web = FakeWebProvider([_web("https://github.example/repo")])
    dependencies = _dependencies(
        FakeGateway("{}"),
        FakeDocumentLister([_document()]),
        FakeSectionSearcher(),
        paper_provider=papers,
        web_provider=web,
    )

    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, _config(dependencies)
    )

    import re as _re

    allowed = {"https://openalex.example/W1", "https://github.example/repo"}
    rendered = " ".join(section["content"] for section in update["sections"])
    found = set(_re.findall(r"https?://\S+", rendered))
    assert found <= allowed, f"invented link(s): {found - allowed}"
```

Create `backend/tests/test_build_plan_research.py`:

```python
"""`research_further` degradation against real provider code.

Drives the actual `OpenAlexProvider` and `TavilyWebResearchProvider`
through `httpx.MockTransport`, so request building, parsing, and the
node's error handling are all exercised -- no network, no API key.
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.agents.planner.nodes import TavilyWebResearchProvider
from app.agents.planner.paper_suggestion import OpenAlexProvider
from app.agents.reports.build_plan_nodes import BuildPlanDependencies, research_further_node
from app.agents.reports.build_plan_state import UNCOVERED

FINDINGS = {
    "method": ["A fine-tuned convolutional classifier."],
    "models": ["ResNet-50"],
    "datasets": ["A private poultry dataset."],
    "metrics": ["Accuracy"],
    "compute": ["One NVIDIA V100."],
    "limitations": ["The dataset is not public."],
}


class _UnusedLister:
    async def list_selected(self, project_id, asset_ids):
        return []


class _UnusedSearcher:
    async def search(self, *, owner_id, project_id, query, limit):
        return []


class _UnusedGateway:
    async def generate(self, **kwargs):
        raise AssertionError("research_further must not call the model.")


def _dependencies(paper_provider, web_provider) -> BuildPlanDependencies:
    return BuildPlanDependencies(
        document_lister=_UnusedLister(),
        section_searcher=_UnusedSearcher(),
        llm_gateway=_UnusedGateway(),
        paper_provider=paper_provider,
        web_provider=web_provider,
    )


def _openalex(handler) -> OpenAlexProvider:
    return OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5.0, transport=httpx.MockTransport(handler)
    )


def _tavily(handler) -> TavilyWebResearchProvider:
    return TavilyWebResearchProvider(
        api_key=SecretStr("test-key"), timeout=5.0, transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_both_providers_missing_a_key_leave_the_report_completable():
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": _dependencies(None, None)}}
    )

    assert update["links"] == []
    assert [section["content"] for section in update["sections"]] == [UNCOVERED, UNCOVERED]
    assert update["step"]["output"] == {
        "openalex": "skipped: no API key configured",
        "tavily": "skipped: no API key configured",
        "link_count": 0,
    }


@pytest.mark.asyncio
async def test_a_failing_openalex_is_recorded_without_leaking_the_key_or_url():
    def openalex_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server exploded"})

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://github.example/repo",
                        "title": "An implementation",
                        "content": "A PyTorch reimplementation.",
                        "score": 0.9,
                    }
                ]
            },
        )

    dependencies = _dependencies(_openalex(openalex_handler), _tavily(tavily_handler))
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": dependencies}}
    )

    payload = json.dumps(update["step"])
    assert "test-key" not in payload
    assert "api.openalex.org" not in payload
    assert update["step"]["output"]["openalex"] == "failed (RuntimeError)"
    # Tavily's results still made it into the document.
    assert update["step"]["output"]["tavily"] == "ok"
    assert "https://github.example/repo" in update["sections"][1]["content"]


@pytest.mark.asyncio
async def test_a_failing_tavily_is_recorded_while_openalex_results_survive():
    def openalex_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "A Better Classifier",
                        "publication_year": 2025,
                        "cited_by_count": 12,
                        "authorships": [{"author": {"display_name": "A. Author"}}],
                        "primary_location": {"landing_page_url": "https://openalex.example/W1"},
                        "best_oa_location": {},
                        "abstract_inverted_index": {"Improves": [0], "accuracy": [1]},
                    }
                ]
            },
        )

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    dependencies = _dependencies(_openalex(openalex_handler), _tavily(tavily_handler))
    update = await research_further_node(
        {"report_id": "r1", "findings": FINDINGS}, {"configurable": {"dependencies": dependencies}}
    )

    assert update["step"]["output"]["openalex"] == "ok"
    assert update["step"]["output"]["tavily"] == "failed (ConnectTimeout)"
    assert "https://openalex.example/W1" in update["sections"][0]["content"]
    assert update["sections"][1]["content"] == UNCOVERED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py tests/test_build_plan_research.py -v`
Expected: FAIL with `NotImplementedError` from `research_further_node`.

- [ ] **Step 3: Implement `research_further_node`**

In `backend/app/agents/reports/build_plan_nodes.py`, replace the `research_further_node` stub:

```python
def build_research_queries(findings: PaperFindings) -> tuple[str, str]:
    """The two provider queries, built from what the paper actually says.

    Two different queries on purpose: OpenAlex is asked for better
    methods (so the query leads with the method and models), Tavily for
    something runnable (so it leads with implementation words and the
    datasets). A single shared query would serve one badly.
    """
    method = "; ".join(findings.get("method") or [])
    models = "; ".join(findings.get("models") or [])
    datasets = "; ".join(findings.get("datasets") or [])
    limitations = "; ".join(findings.get("limitations") or [])

    scholarly = " ".join(
        part for part in [method, models, "improved method", limitations] if part
    )
    practical = " ".join(
        part
        for part in ["open source implementation library", method, models, datasets]
        if part
    )
    return scholarly.strip(), practical.strip()


def render_links_section(links: list[ResearchLink]) -> str:
    """Render collected provider links as the body of one section.

    The only place a URL is written into a section (design ruling R5):
    every line comes from a `ResearchLink` a provider returned.
    """
    lines = []
    for link in links:
        lines.append(f"{link['title']}")
        lines.append(f"  {link['url']}")
        if link["note"]:
            lines.append(f"  {link['note']}")
    return "\n".join(lines)


async def research_further_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    """Search OpenAlex for follow-up methods and Tavily for runnable
    resources, degrading per provider.

    Non-critical by registry, and additionally self-healing: each
    provider is attempted independently and its outcome recorded, so a
    missing key or a raising provider costs only that provider's
    section rather than the whole report (design ruling R4, spec
    sections 6 and 7). Nothing raised here is ever re-raised, and no
    provider's own message is recorded -- only its exception type.
    """
    dependencies = _dependencies(config)
    findings = state.get("findings") or PaperFindings(
        method=[], models=[], datasets=[], metrics=[], compute=[], limitations=[]
    )
    scholarly_query, practical_query = build_research_queries(findings)

    paper_links: list[ResearchLink] = []
    paper_status = "skipped: no API key configured"
    if dependencies.paper_provider is not None:
        try:
            papers = await dependencies.paper_provider.search(
                query=scholarly_query, limit=PROVIDER_RESULT_LIMIT
            )
            for paper in papers:
                authors = ", ".join(paper["authors"][:3]) or "Unknown authors"
                year = paper["year"] or "year unknown"
                paper_links.append(
                    ResearchLink(
                        origin="openalex",
                        title=f"{paper['title']} ({authors}, {year})",
                        # `landing_url` is the publisher page OpenAlex
                        # itself returned -- never a constructed URL.
                        url=paper["oa_pdf_url"] or paper["landing_url"],
                        note=f"Cited {paper['cited_by_count']} time(s). {paper['relevance_note']}",
                    )
                )
            paper_status = "ok"
        except Exception as exc:
            # Type name only. `OpenAlexProvider` already scrubs its own
            # HTTP errors, but a transport-level exception's message can
            # carry the request URL -- and that URL carries the api_key
            # query parameter.
            paper_status = f"failed ({type(exc).__name__})"
            logger.warning(
                "build_plan_openalex_failed",
                report_id=state.get("report_id"),
                error_type=type(exc).__name__,
            )

    web_links: list[ResearchLink] = []
    web_status = "skipped: no API key configured"
    if dependencies.web_provider is not None:
        try:
            documents = await dependencies.web_provider.search(
                query=practical_query, limit=PROVIDER_RESULT_LIMIT
            )
            for document in documents:
                # A simulated result has no real link; the spec forbids
                # standing one in for a real one, so it is dropped even
                # if a simulating provider somehow got this far.
                if document.get("simulated"):
                    continue
                web_links.append(
                    ResearchLink(
                        origin="tavily",
                        title=document.get("title") or document["reference"],
                        url=document["reference"],
                        note=(document.get("snippet") or "")[:280],
                    )
                )
            web_status = "ok"
        except Exception as exc:
            web_status = f"failed ({type(exc).__name__})"
            logger.warning(
                "build_plan_tavily_failed",
                report_id=state.get("report_id"),
                error_type=type(exc).__name__,
            )

    links = [*paper_links, *web_links]
    logger.info(
        "build_plan_research_completed",
        report_id=state.get("report_id"),
        openalex=paper_status,
        tavily=web_status,
        link_count=len(links),
    )
    return {
        "links": links,
        "sections": [
            _section(
                BUILD_PLAN_SECTION_TITLES[1],
                render_links_section(paper_links),
                [],
            ),
            _section(
                BUILD_PLAN_SECTION_TITLES[2],
                render_links_section(web_links),
                [],
            ),
        ],
        "step": {
            "summary": (
                f"OpenAlex: {paper_status}. Tavily: {web_status}. "
                f"{len(links)} link(s) found."
            ),
            "output": {
                "openalex": paper_status,
                "tavily": web_status,
                "link_count": len(links),
            },
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py tests/test_build_plan_research.py -v`
Expected: PASS — all seven node tests and all three degradation tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/reports/build_plan_nodes.py backend/tests/test_build_plan_nodes.py backend/tests/test_build_plan_research.py
git commit -m "feat(reports): research follow-up methods and implementations, degrading per provider

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: The `recommend_tools` and `recommend_process` nodes

**Files:**
- Modify: `backend/app/agents/reports/build_plan_nodes.py`
- Test: `backend/tests/test_build_plan_nodes.py`

**Interfaces:**
- Consumes: `ToolsDraft`, `ProcessDraft`, `render_tools_prompt`, `render_process_prompt`, `TOOLS_SYSTEM_PROMPT`, `PROCESS_SYSTEM_PROMPT`, `TOOL_STAGES`, `strip_urls`, `_section`, `_parse`.
- Produces: `recommend_tools_node` writing `tools` and the `"Recommended Tools"` section; `recommend_process_node` writing `phases` and the `"Build Process"` section; `render_tools_section(tools)`; `render_phases_section(phases)`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_build_plan_nodes.py`:

```python
from app.agents.reports.build_plan_nodes import recommend_process_node, recommend_tools_node

TOOLS_JSON = json.dumps(
    {
        "tools": [
            {
                "stage": "data",
                "name": "Label Studio",
                "reason": "The paper's 4,000-image dataset is private, so you must label your own.",
                "alternatives": ["CVAT", "Roboflow"],
            },
            {
                "stage": "modelling",
                "name": "PyTorch",
                "reason": "ResNet-50 pretrained weights ship with torchvision.",
                "alternatives": ["TensorFlow"],
            },
            {
                "stage": "not-a-stage",
                "name": "Something",
                "reason": "Invented stage.",
                "alternatives": [],
            },
        ]
    }
)

PROCESS_JSON = json.dumps(
    {
        "phases": [
            {
                "name": "Reproduce the paper's baseline",
                "steps": ["Obtain a comparable dataset.", "Fine-tune ResNet-50."],
                "definition_of_done": "Accuracy within 2 points of the paper's reported figure.",
                "risks": ["The dataset needs licence approval before use."],
            },
            {
                "name": "Ship a working product",
                "steps": ["Wrap the model in a FastAPI endpoint."],
                "definition_of_done": "A user can upload an image and get a prediction.",
                "risks": [],
            },
        ]
    }
)


@pytest.mark.asyncio
async def test_recommend_tools_groups_by_stage_with_reasons_and_alternatives():
    gateway = FakeGateway(TOOLS_JSON)
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher())

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    section = update["sections"][0]
    assert section["title"] == "Recommended Tools"
    assert section["covered"] is True
    # Grouped under the stage headings, in the spec's order.
    assert section["content"].index("Data") < section["content"].index("Modelling")
    assert "Label Studio" in section["content"]
    assert "Alternatives: CVAT, Roboflow" in section["content"]
    assert "4,000-image dataset is private" in section["content"]
    # A stage the model invented is dropped rather than rendered.
    assert "not-a-stage" not in section["content"]
    assert [tool["stage"] for tool in update["tools"]] == ["data", "modelling"]


@pytest.mark.asyncio
async def test_recommend_tools_marks_the_section_uncovered_when_the_model_returns_none():
    gateway = FakeGateway(json.dumps({"tools": []}))
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher())

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    assert update["sections"][0]["content"] == UNCOVERED
    assert update["sections"][0]["covered"] is False


@pytest.mark.asyncio
async def test_recommend_process_produces_phases_with_done_criteria_and_risks():
    gateway = FakeGateway(PROCESS_JSON)
    dependencies = _dependencies(gateway, FakeDocumentLister([_document()]), FakeSectionSearcher())

    update = await recommend_process_node(
        {
            "report_id": "r1",
            "findings": FINDINGS,
            "tools": [
                {"stage": "modelling", "name": "PyTorch", "reason": "ResNet-50", "alternatives": []}
            ],
        },
        _config(dependencies),
    )

    section = update["sections"][0]
    assert section["title"] == "Build Process"
    assert section["covered"] is True
    assert "Phase 1: Reproduce the paper's baseline" in section["content"]
    assert "Definition of done: Accuracy within 2 points" in section["content"]
    assert "licence approval" in section["content"]
    assert len(update["phases"]) == 2


@pytest.mark.asyncio
async def test_a_model_written_url_never_reaches_a_section():
    leaky = json.dumps(
        {
            "tools": [
                {
                    "stage": "modelling",
                    "name": "PyTorch",
                    "reason": "See https://invented.example/not-real for details.",
                    "alternatives": ["JAX (www.invented.example)"],
                }
            ]
        }
    )
    dependencies = _dependencies(
        FakeGateway(leaky), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    update = await recommend_tools_node(
        {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
    )

    content = update["sections"][0]["content"]
    assert "invented.example" not in content
    assert "PyTorch" in content


@pytest.mark.asyncio
async def test_an_unparseable_tools_response_fails_the_node():
    dependencies = _dependencies(
        FakeGateway("not json at all"), FakeDocumentLister([_document()]), FakeSectionSearcher()
    )

    with pytest.raises(RuntimeError, match="Could not parse the tool-recommendation response"):
        await recommend_tools_node(
            {"report_id": "r1", "findings": FINDINGS, "links": []}, _config(dependencies)
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py -v -k "recommend or url or unparseable"`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement both nodes**

In `backend/app/agents/reports/build_plan_nodes.py`, replace both stubs:

```python
def render_tools_section(tools: list[ToolRecommendation]) -> str:
    """Render tool recommendations grouped under stage headings.

    Grouped in `TOOL_STAGES` order rather than the model's order, so the
    document always reads data -> modelling -> backend -> evaluation ->
    deployment regardless of what order the model happened to answer in.
    """
    lines = []
    for stage in TOOL_STAGES:
        in_stage = [tool for tool in tools if tool["stage"] == stage]
        if not in_stage:
            continue
        lines.append(f"{stage.capitalize()}")
        for tool in in_stage:
            lines.append(f"  {tool['name']}")
            lines.append(f"    Why: {tool['reason']}")
            if tool["alternatives"]:
                lines.append(f"    Alternatives: {', '.join(tool['alternatives'])}")
    return "\n".join(lines)


def render_phases_section(phases: list[BuildPhase]) -> str:
    """Render build phases, numbered, with steps, done criteria and risks."""
    lines = []
    for position, phase in enumerate(phases, start=1):
        lines.append(f"Phase {position}: {phase['name']}")
        for step in phase["steps"]:
            lines.append(f"  - {step}")
        if phase["definition_of_done"]:
            lines.append(f"  Definition of done: {phase['definition_of_done']}")
        if phase["risks"]:
            lines.append("  Risks:")
            lines.extend(f"    - {risk}" for risk in phase["risks"])
    return "\n".join(lines)


async def recommend_tools_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    """Recommend tools per build stage, each tied to a paper finding.

    Critical: a build plan whose tool section could not be produced is
    not a build plan, so an unparseable response fails the whole report
    rather than shipping a document with a hole in it.
    """
    dependencies = _dependencies(config)
    findings = state.get("findings") or PaperFindings(
        method=[], models=[], datasets=[], metrics=[], compute=[], limitations=[]
    )
    links = state.get("links", [])

    response = await dependencies.llm_gateway.generate(
        prompt=render_tools_prompt(findings=findings, links=links),
        system_prompt=TOOLS_SYSTEM_PROMPT,
        model=settings.synthesis_model,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "ToolsDraft", "schema": ToolsDraft.model_json_schema()},
        },
    )
    draft: ToolsDraft = _parse(ToolsDraft, response.content, "tool-recommendation")

    tools: list[ToolRecommendation] = []
    for tool in draft.tools:
        stage = tool.stage.strip().lower()
        # A stage the model invented has nowhere to render and would
        # silently vanish from the grouped output; drop it here so the
        # step count and the document agree.
        if stage not in TOOL_STAGES:
            logger.warning(
                "build_plan_tool_stage_rejected",
                report_id=state.get("report_id"),
                stage=stage,
            )
            continue
        tools.append(
            ToolRecommendation(
                stage=stage,
                name=strip_urls(tool.name),
                reason=strip_urls(tool.reason),
                alternatives=[
                    stripped
                    for stripped in (strip_urls(alternative) for alternative in tool.alternatives)
                    if stripped
                ],
            )
        )

    stages_covered = sorted({tool["stage"] for tool in tools})
    logger.info(
        "build_plan_tools_recommended",
        report_id=state.get("report_id"),
        tool_count=len(tools),
        stage_count=len(stages_covered),
    )
    return {
        "tools": tools,
        "sections": [_section(BUILD_PLAN_SECTION_TITLES[3], render_tools_section(tools), [])],
        "step": {
            "summary": f"Recommended {len(tools)} tool(s) across {len(stages_covered)} stage(s).",
            "output": {
                "tool_count": len(tools),
                "stages": stages_covered,
                "llm_calls": 1,
            },
        },
    }


async def recommend_process_node(state: BuildPlanState, config: RunnableConfig) -> dict[str, Any]:
    """Lay out phases from reproducing the paper's baseline to a product."""
    dependencies = _dependencies(config)
    findings = state.get("findings") or PaperFindings(
        method=[], models=[], datasets=[], metrics=[], compute=[], limitations=[]
    )
    tools = state.get("tools", [])

    response = await dependencies.llm_gateway.generate(
        prompt=render_process_prompt(findings=findings, tools=tools),
        system_prompt=PROCESS_SYSTEM_PROMPT,
        model=settings.synthesis_model,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "ProcessDraft", "schema": ProcessDraft.model_json_schema()},
        },
    )
    draft: ProcessDraft = _parse(ProcessDraft, response.content, "build-process")

    phases = [
        BuildPhase(
            name=strip_urls(phase.name),
            steps=[stripped for stripped in (strip_urls(step) for step in phase.steps) if stripped],
            definition_of_done=strip_urls(phase.definition_of_done),
            risks=[stripped for stripped in (strip_urls(risk) for risk in phase.risks) if stripped],
        )
        for phase in draft.phases
    ]

    risk_count = sum(len(phase["risks"]) for phase in phases)
    logger.info(
        "build_plan_process_recommended",
        report_id=state.get("report_id"),
        phase_count=len(phases),
        risk_count=risk_count,
    )
    return {
        "phases": phases,
        "sections": [_section(BUILD_PLAN_SECTION_TITLES[4], render_phases_section(phases), [])],
        "step": {
            "summary": f"Laid out {len(phases)} phase(s) with {risk_count} risk(s).",
            "output": {"phase_count": len(phases), "risk_count": risk_count, "llm_calls": 1},
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_nodes.py tests/test_build_plan_research.py -v`
Expected: PASS — every node test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/reports/build_plan_nodes.py backend/tests/test_build_plan_nodes.py
git commit -m "feat(reports): recommend per-stage tools and a phased build process

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: The endpoint — schema, service, router

**Files:**
- Modify: `backend/app/modules/reports/schemas.py`
- Modify: `backend/app/modules/reports/service.py`
- Modify: `backend/app/modules/reports/router.py`
- Test: `backend/tests/test_build_plan_routes.py`

**Interfaces:**
- Consumes: `ReportGenerationAccepted`, `ProjectAccessDeniedError`, `NoProcessedDocumentsError`, `ReportRepository.list_processed_documents_by_ids` (Task 2), `generate_report`.
- Produces: `BuildPlanRequest(asset_ids: list[uuid.UUID])`, `BUILD_PLAN_KIND = "build_plan"`, `ReportService.generate_build_plan(owner_id, project_id, asset_ids) -> Asset`, `AssetSelectionError`, and `POST /api/v1/projects/{project_id}/reports/build-plan`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_plan_routes.py`:

```python
"""Route-level tests for the build-plan endpoint (Milestone 10 step 5).

Drives the real app through `httpx.ASGITransport` against the real
database, following `tests/test_project_persona.py` and
`tests/test_reports_routes.py`. The Celery enqueue is replaced; no LLM
is ever called. Every user created here is deleted in a `finally`
(cascading to projects and assets).
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete

from app.database.session import async_session_factory
from app.main import app
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.auth.models import User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def enqueued(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.modules.reports.service.generate_report",
        SimpleNamespace(delay=lambda asset_id: calls.append(asset_id)),
    )
    return calls


async def _register(client: httpx.AsyncClient, emails: list[str]) -> dict:
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    emails.append(email)
    await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "persona": "builder"}
    )
    tokens = (
        await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_project(client: httpx.AsyncClient, headers: dict) -> uuid.UUID:
    created = (
        await client.post(
            "/api/v1/projects", json={"name": "Build plan route test"}, headers=headers
        )
    ).json()
    return uuid.UUID(created["id"])


async def _document(
    client: httpx.AsyncClient,
    headers: dict,
    project_id: uuid.UUID,
    *,
    processed: bool = True,
) -> uuid.UUID:
    """Insert one DOCUMENT asset directly -- the upload pipeline is not
    what these tests exercise, and a real upload would run extraction."""
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    owner_id = uuid.UUID(me["id"])
    async with async_session_factory() as session:
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title="A Paper",
            description=None,
            asset_type=AssetType.DOCUMENT,
            status=AssetStatus.ACTIVE,
            mime_type="application/pdf",
            file_name="paper.pdf",
            file_extension="pdf",
            file_size=1024,
            storage_path="/tmp/paper.pdf",
            checksum="abc",
            source=AssetSource.UPLOAD,
            version=1,
            tags=[],
            asset_metadata={},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=(
                AssetProcessingStatus.COMPLETED if processed else AssetProcessingStatus.PENDING
            ),
        )
        session.add(asset)
        await session.commit()
        return asset.id


async def _cleanup(emails: list[str]) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email.in_(emails)))
        await session.commit()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_build_plan_accepts_a_selection_and_enqueues_one_report(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        asset_id = await _document(client, headers, project_id)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(asset_id)]},
            headers=headers,
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert enqueued == [body["asset_id"]]

        # The report is a GENERATED REPORT asset carrying the build-plan kind.
        report = (await client.get(f"/api/v1/reports/{body['asset_id']}", headers=headers)).json()
        assert report["asset_type"] == "report"
        assert report["source"] == "generated"
        assert report["metadata"]["kind"] == "build_plan"
        assert report["metadata"]["asset_ids"] == [str(asset_id)]
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_404s_for_another_users_project(client, enqueued):
    emails: list[str] = []
    try:
        owner_headers = await _register(client, emails)
        project_id = await _create_project(client, owner_headers)
        asset_id = await _document(client, owner_headers, project_id)

        intruder_headers = await _register(client, emails)
        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(asset_id)]},
            headers=intruder_headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found."
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_404s_for_an_asset_from_another_project(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        await _document(client, headers, project_id)

        other_project_id = await _create_project(client, headers)
        foreign_asset_id = await _document(client, headers, other_project_id)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(foreign_asset_id)]},
            headers=headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found."
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_422s_when_the_project_has_no_processed_documents(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        unprocessed_id = await _document(client, headers, project_id, processed=False)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(unprocessed_id)]},
            headers=headers,
        )

        assert response.status_code == 422
        assert "no processed documents" in response.json()["detail"]
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_422s_for_an_empty_selection(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        await _document(client, headers, project_id)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": []},
            headers=headers,
        )

        assert response.status_code == 422
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_422s_when_the_selection_names_only_unprocessed_documents(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        # The project does have a processed document, so this is not the
        # "no processed documents" case -- the selection itself is unusable.
        await _document(client, headers, project_id)
        unprocessed_id = await _document(client, headers, project_id, processed=False)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(unprocessed_id)]},
            headers=headers,
        )

        assert response.status_code == 422
        assert "still processing" in response.json()["detail"]
        assert enqueued == []
    finally:
        await _cleanup(emails)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_routes.py -v`
Expected: FAIL with `404` on every request (no such route) / `AttributeError` on `BuildPlanRequest`.

- [ ] **Step 3: Add `BuildPlanRequest` to `schemas.py`**

Append to `backend/app/modules/reports/schemas.py`:

```python
#: The `asset_metadata["kind"]` marker for a build plan. Deliberately a
#: plain string rather than a `ReportKind` member (design ruling R1): a
#: build plan is its own endpoint with its own graph, and widening
#: `ReportKind` would make it a valid body for the synopsis endpoint.
BUILD_PLAN_KIND = "build_plan"


class BuildPlanRequest(BaseModel):
    """Payload for `POST /projects/{project_id}/reports/build-plan`.

    `min_length=1` rejects an empty selection with FastAPI's own `422`
    before any handler runs -- the spec requires at least one paper, and
    an empty list must never be read as "all documents".
    """

    asset_ids: list[uuid.UUID] = Field(min_length=1)
```

- [ ] **Step 4: Add `generate_build_plan` to `service.py`**

In `backend/app/modules/reports/service.py`, add the import and error class, then the method.

Add to the imports:

```python
from app.modules.reports.schemas import BUILD_PLAN_KIND, ReportKind
```

(replacing the existing `from app.modules.reports.schemas import ReportKind`)

Add after `NoProcessedDocumentsError`:

```python
class AssetSelectionError(Exception):
    """Raised when a build-plan selection contains no usable document.

    Distinct from `NoProcessedDocumentsError`: the project does have
    processed documents, but the papers the caller picked are not among
    them -- a different message, and a different thing for the user to
    fix.
    """


class AssetNotInProjectError(Exception):
    """Raised when a selected asset id is not in the given project.

    Surfaces as `404`, not `403`: consistent with the rest of the
    module, "not yours" and "doesn't exist" are indistinguishable to
    the caller (spec section 7).
    """
```

Add after `generate_synopsis`:

```python
    async def generate_build_plan(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> Asset:
        """Create a `pending` build-plan asset and dispatch its generation.

        Validation order matters and is deliberate:

        1. project ownership -> `404`, so nothing about a project the
           caller cannot see is ever revealed;
        2. any selected id not in that project -> `404`, same reason;
        3. the project having no processed documents at all -> `422`;
        4. the selection naming no processed document -> `422`.

        Steps 3 and 4 are separate because they are different user
        problems: "upload something" versus "wait for processing". As
        with `generate_synopsis`, every check runs before any asset or
        Celery job exists, so a rejection leaves nothing behind.
        """
        await self._ensure_project_owned(owner_id, project_id)

        in_project = await self._reports.list_project_asset_ids(project_id)
        unknown = [asset_id for asset_id in asset_ids if asset_id not in in_project]
        if unknown:
            raise AssetNotInProjectError(unknown)

        processed = await self._reports.list_processed_documents(project_id)
        if not processed:
            raise NoProcessedDocumentsError(project_id)

        selected = await self._reports.list_processed_documents_by_ids(project_id, asset_ids)
        if not selected:
            raise AssetSelectionError(asset_ids)

        title = "Build Plan"
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title=title,
            description=None,
            asset_type=AssetType.REPORT,
            status=AssetStatus.ACTIVE,
            # As with a synopsis, no file is stored: the document is
            # rendered on demand from `asset_metadata["sections"]`.
            mime_type="application/json",
            file_name=f"{_slug(title)}.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            # `asset_ids` is persisted, not just passed to the worker: a
            # retry re-runs from the asset row alone, and the trace
            # should record which papers this plan was built from.
            asset_metadata={
                "kind": BUILD_PLAN_KIND,
                "asset_ids": [str(asset_id) for asset_id in asset_ids],
                "sections": [],
            },
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.PENDING,
        )
        created = await self._assets.create(asset)
        await self._session.commit()

        generate_report.delay(str(created.id))
        return created
```

This uses `ReportRepository.list_project_asset_ids` from Task 2 Step 5 — confirmed during planning that `AssetRepository` exposes no project-wide listing method (only `get_by_id`, `search`, `find_active_by_checksum`, `find_by_execution_attempt`, `create`, `delete`), which is why the membership query lives on `ReportRepository`.

- [ ] **Step 5: Add the route to `router.py`**

Add to the imports in `backend/app/modules/reports/router.py`:

```python
from app.modules.reports.schemas import (
    BuildPlanRequest,
    ReportGenerateRequest,
    ReportGenerationAccepted,
    ReportRead,
)
from app.modules.reports.service import (
    AssetNotInProjectError,
    AssetSelectionError,
    NoProcessedDocumentsError,
    ProjectAccessDeniedError,
    ReportNotFoundError,
    ReportNotReadyError,
    ReportNotRetryableError,
    ReportService,
    get_report_service,
)
```

Add the route after `generate_synopsis_route`:

```python
@router.post(
    "/projects/{project_id}/reports/build-plan",
    response_model=ReportGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_build_plan_route(
    project_id: uuid.UUID,
    data: BuildPlanRequest,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportGenerationAccepted:
    """Start generating a build plan from the selected papers. Runs in
    the Celery worker; poll `GET /reports/{asset_id}` for
    `processing_status`, then download via
    `GET /reports/{asset_id}/download`."""
    try:
        asset = await service.generate_build_plan(current_user.id, project_id, data.asset_ids)
    except (ProjectAccessDeniedError, AssetNotInProjectError) as exc:
        # One 404 for both: a selection naming an asset outside the
        # project must not reveal whether that asset exists elsewhere.
        raise _PROJECT_NOT_FOUND from exc
    except NoProcessedDocumentsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This project has no processed documents to build a plan from yet.",
        ) from exc
    except AssetSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The papers you selected are still processing. Pick one that has finished.",
        ) from exc
    return ReportGenerationAccepted(asset_id=asset.id, status=asset.processing_status)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_routes.py tests/test_reports_routes.py -v`
Expected: PASS — six new route tests plus the step-4 route tests unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/modules/reports/schemas.py backend/app/modules/reports/service.py backend/app/modules/reports/router.py backend/tests/test_build_plan_routes.py
git commit -m "feat(reports): add the build-plan endpoint with ownership and selection validation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Worker dispatch and the export check

**Files:**
- Modify: `backend/app/workers/tasks.py`
- Test: `backend/tests/test_build_plan_export.py`

**Interfaces:**
- Consumes: `get_build_plan_graph`, `build_build_plan_dependencies`, `BUILD_PLAN_AGENT_REGISTRY`, `BUILD_PLAN_KIND`, `ReportStepTracker(..., registry=)`, `render_docx`, `render_pdf`.
- Produces: `_generate_report` routing on `asset_metadata["kind"]`; no new public names.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_plan_export.py`:

```python
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
```

`pypdf` is the reader `tests/test_reports_export.py` already uses (confirmed during planning) — no new dependency.

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_export.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents.reports.build_plan_state'` only if Task 1 was skipped; otherwise these two tests should already PASS, proving the export path needs no change. If they pass, record that in the commit message and move to Step 3.

- [ ] **Step 3: Route the Celery task on the stored kind**

In `backend/app/workers/tasks.py`, add to the imports near the existing report imports:

```python
from app.agents.reports.build_plan_graph import get_build_plan_graph
from app.agents.reports.build_plan_nodes import build_build_plan_dependencies
from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.modules.reports.schemas import BUILD_PLAN_KIND
```

In `_generate_report`, replace the block from `attempt = ...` through `await session.commit()` inside the `try` with:

```python
            # Each run is its own attempt, numbered after any earlier ones,
            # so a retried report keeps every attempt's trace separately.
            attempt = await ResearchStepRepository(session).latest_attempt(asset_id) + 1
            kind = asset.asset_metadata.get("kind")

            if kind == BUILD_PLAN_KIND:
                # One task drives both report graphs (design ruling R1):
                # the claim, the attempt numbering, the failure path and
                # the terminal statuses are identical, so only the graph,
                # its dependencies, its registry and its inputs differ.
                tracker = ReportStepTracker(
                    session, asset_id, attempt=attempt, registry=BUILD_PLAN_AGENT_REGISTRY
                )
                graph = get_build_plan_graph()
                dependencies = build_build_plan_dependencies(session)
                inputs = {
                    "report_id": str(asset.id),
                    "project_id": str(asset.project_id),
                    "owner_id": str(asset.owner_id),
                    "asset_ids": list(asset.asset_metadata.get("asset_ids") or []),
                }
            else:
                tracker = ReportStepTracker(session, asset_id, attempt=attempt)
                graph = get_report_graph()
                dependencies = build_report_dependencies(session)
                inputs = {
                    "report_id": str(asset.id),
                    "project_id": str(asset.project_id),
                    "owner_id": str(asset.owner_id),
                    "kind": kind,
                }

            result = await graph.ainvoke(
                inputs,
                config={"configurable": {"dependencies": dependencies, TRACKER_CONFIG_KEY: tracker}},
            )
            asset.asset_metadata = {**asset.asset_metadata, "sections": result["sections"]}
            asset.processing_status = AssetProcessingStatus.COMPLETED
            asset.processing_error = None
            await session.commit()
```

- [ ] **Step 4: Restart the worker and verify the whole report suite**

```bash
docker compose restart worker
```

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_export.py tests/test_build_plan_nodes.py tests/test_build_plan_research.py tests/test_build_plan_routes.py tests/test_reports_export.py tests/test_reports_graph.py tests/test_reports_routes.py tests/test_reports_service.py tests/test_report_steps.py tests/test_report_dependencies.py tests/test_report_generation_reconciliation.py -v`
Expected: PASS — every build-plan test and every step-4 report test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/tasks.py backend/tests/test_build_plan_export.py
git commit -m "feat(reports): run the build-plan graph from the shared report task

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Regenerate API types and extract the shared run panel

**Files:**
- Modify: `frontend/src/types/api.ts` (regenerated — do not hand-edit)
- Create: `frontend/src/features/reports/ReportRunPanel.tsx`
- Modify: `frontend/src/features/reports/GenerateSynopsisDialog.tsx`
- Modify: `frontend/src/services/reports.ts`

**Interfaces:**
- Consumes: `reportsService.getReport`, `retryReport`, `downloadReport`, `usePolling`, `ResearchPipeline`, `StatusBadge`, `messageFor`.
- Produces: `generateBuildPlan(projectId, assetIds)`, `<ReportRunPanel report timedOut onRetry retrying retryError />`, and `POLL_TIMEOUT_MS` exported from `ReportRunPanel`.

- [ ] **Step 1: Regenerate the API types**

The backend runs in Docker on `:8001`; the endpoint from Task 5 must already be live.

```bash
cd frontend && npm run generate:api
```

Verify the new schema landed:

```bash
grep -n "BuildPlanRequest" frontend/src/types/api.ts
```

Expected: a `BuildPlanRequest` schema with an `asset_ids` array. If the grep finds nothing, the backend did not reload — `docker compose restart backend`, then re-run.

- [ ] **Step 2: Add the service function**

Append to `frontend/src/services/reports.ts`:

```typescript
type BuildPlanRequest = components["schemas"]["BuildPlanRequest"];

export function generateBuildPlan(projectId: string, assetIds: string[]) {
  return request<ReportGenerationAccepted>(`/api/v1/projects/${projectId}/reports/build-plan`, {
    method: "POST",
    body: { asset_ids: assetIds } satisfies BuildPlanRequest,
  });
}
```

- [ ] **Step 3: Create `ReportRunPanel.tsx` by moving the synopsis dialog's run UI verbatim**

```tsx
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/common/StatusBadge";
import { ResearchPipeline } from "@/features/research/ResearchPipeline";
import { messageFor } from "@/lib/api-error";
import * as reportsService from "@/services/reports";
import { useState } from "react";
import type { components } from "@/types/api";

type ReportRead = components["schemas"]["ReportRead"];
type ReportStep = NonNullable<ReportRead["steps"]>[number];

// ponytail: fixed 6-minute cap, not configurable per-report -- mirrors
// `ResearchRunView.POLL_TIMEOUT_MS`'s reasoning for a hard safety net,
// scaled down since a report is a handful of LLM calls, not a full
// research run.
export const POLL_TIMEOUT_MS = 6 * 60 * 1000;

export function isReportTerminal(report: ReportRead): boolean {
  return report.processing_status === "completed" || report.processing_status === "failed";
}

/** The report's steps grouped by generation attempt, oldest first --
 * each attempt is its own pipeline run (1, then +1 per retry). */
export function stepsByAttempt(steps: ReportStep[]): [number, ReportStep[]][] {
  const groups = new Map<number, ReportStep[]>();
  for (const step of steps) {
    groups.set(step.attempt, [...(groups.get(step.attempt) ?? []), step]);
  }
  return [...groups.entries()].sort(([a], [b]) => a - b);
}

/** Everything a report run looks like once it has started: progress,
 * the attempt-grouped pipeline, the timeout notice, failure + retry,
 * and the two download buttons.
 *
 * Extracted from `GenerateSynopsisDialog` rather than reimplemented, so
 * the synopsis and the build plan cannot drift into two different
 * progress UIs (design ruling R7). The dialogs keep only what differs:
 * their own picker and their own generate mutation. */
export function ReportRunPanel({
  report,
  timedOut,
  onRetry,
  retrying,
  retryError,
}: {
  report: ReportRead | undefined;
  timedOut: boolean;
  onRetry: (assetId: string) => void;
  retrying: boolean;
  retryError: unknown;
}) {
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const failed = report?.processing_status === "failed";
  const completed = report?.processing_status === "completed";
  const inProgress = report !== undefined && !completed && !failed && !timedOut;

  function handleDownload(id: string, format: "docx" | "pdf", filename: string) {
    setDownloadError(null);
    reportsService.downloadReport(id, format, filename).catch((error: unknown) => {
      setDownloadError(messageFor(error));
    });
  }

  return (
    <>
      {inProgress && (
        <div className="flex items-center gap-2" role="status">
          <StatusBadge domain="assetProcessing" value={report?.processing_status ?? "pending"} />
          <p className="text-sm text-muted-foreground">Generating your report…</p>
        </div>
      )}

      {report && report.steps && report.steps.length > 0 && (
        <div className="flex max-h-72 flex-col gap-4 overflow-y-auto rounded-lg bg-sunken p-4">
          {stepsByAttempt(report.steps).map(([attempt, steps], index, groups) => (
            <section key={attempt} aria-label={`Attempt ${attempt}`}>
              {/* Headings only once a report has been retried: a
               * single run needs no "Attempt 1" label. */}
              {groups.length > 1 && (
                <p className="mb-2 text-label uppercase text-muted-foreground">
                  Attempt {attempt}
                  {index === groups.length - 1 ? " (latest)" : ""}
                </p>
              )}
              <ResearchPipeline steps={steps} />
            </section>
          ))}
        </div>
      )}

      {timedOut && (
        <p role="alert" className="text-sm text-destructive">
          This is taking longer than expected. Check back later in Assets.
        </p>
      )}

      {failed && (
        <div className="flex flex-col gap-2">
          <p role="alert" className="text-sm text-destructive">
            {report?.processing_error ?? "Report generation failed."}
          </p>
          {retryError !== null && retryError !== undefined && (
            <p role="alert" className="text-sm text-destructive">
              {messageFor(retryError)}
            </p>
          )}
          <div>
            <Button variant="outline" disabled={retrying} onClick={() => report && onRetry(report.id)}>
              {retrying ? "Retrying…" : "Retry"}
            </Button>
          </div>
        </div>
      )}

      {completed && report && (
        <div className="flex gap-2" role="status">
          <Button
            variant="outline"
            onClick={() => handleDownload(report.id, "docx", `${report.title}.docx`)}
          >
            Download DOCX
          </Button>
          <Button
            variant="outline"
            onClick={() => handleDownload(report.id, "pdf", `${report.title}.pdf`)}
          >
            Download PDF
          </Button>
        </div>
      )}

      {downloadError && (
        <p role="alert" className="text-sm text-destructive">
          {downloadError}
        </p>
      )}
    </>
  );
}
```

- [ ] **Step 4: Rewrite `GenerateSynopsisDialog.tsx` to consume the panel**

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  POLL_TIMEOUT_MS,
  ReportRunPanel,
  isReportTerminal,
} from "@/features/reports/ReportRunPanel";
import { usePolling } from "@/hooks/usePolling";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type ReportKind = components["schemas"]["ReportKind"];

const KIND_OPTIONS: { value: ReportKind; label: string; description: string }[] = [
  {
    value: "study_summary",
    label: "Study summary",
    description: "Key themes, main findings per document, and how they relate.",
  },
  {
    value: "project_synopsis",
    label: "Project synopsis",
    description: "A formal write-up: abstract, methodology, literature review, and more.",
  },
];

export function GenerateSynopsisDialog({
  open,
  onOpenChange,
  projectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
}) {
  const [kind, setKind] = useState<ReportKind | null>(null);
  const [assetId, setAssetId] = useState<string | null>(null);

  const generateMutation = useMutation({
    mutationFn: (selected: ReportKind) => reportsService.generateSynopsis(projectId, selected),
    onSuccess: (accepted) => setAssetId(accepted.asset_id),
  });

  const queryClient = useQueryClient();
  // ponytail: the 6-minute poll timeout keeps counting from the first
  // attempt; a retry after a timeout needs a reopened dialog. Reset
  // `usePolling`'s timer per attempt if that ever matters.
  const retryMutation = useMutation({
    mutationFn: (id: string) => reportsService.retryReport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", "report", assetId] }),
  });

  const reportQuery = usePolling({
    queryKey: ["reports", "report", assetId],
    queryFn: () => reportsService.getReport(assetId as string),
    isTerminal: isReportTerminal,
    enabled: assetId !== null,
    timeoutMs: POLL_TIMEOUT_MS,
  });

  function reset() {
    setKind(null);
    setAssetId(null);
    generateMutation.reset();
    retryMutation.reset();
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Generate synopsis</DialogTitle>
          <DialogDescription>
            Creates a report from this project&apos;s processed documents.
          </DialogDescription>
        </DialogHeader>

        {!assetId && (
          <div className="flex flex-col gap-3">
            {KIND_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setKind(option.value)}
                aria-pressed={kind === option.value}
                className={`rounded-md border p-3 text-left transition-colors ${
                  kind === option.value ? "border-primary bg-primary/5" : "border-border"
                }`}
              >
                <p className="font-medium">{option.label}</p>
                <p className="text-sm text-muted-foreground">{option.description}</p>
              </button>
            ))}
          </div>
        )}

        <ReportRunPanel
          report={reportQuery.data}
          timedOut={reportQuery.timedOut}
          onRetry={(id) => retryMutation.mutate(id)}
          retrying={retryMutation.isPending}
          retryError={retryMutation.isError ? retryMutation.error : null}
        />

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Close
          </Button>
          {!assetId && (
            <Button
              disabled={!kind || generateMutation.isPending}
              onClick={() => kind && generateMutation.mutate(kind)}
            >
              {generateMutation.isPending ? "Starting…" : "Generate"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: Run the existing synopsis tests to prove the extraction changed nothing**

Run: `cd frontend && npx vitest run src/features/reports/GenerateSynopsisDialog.test.tsx src/features/projects/ProjectHeader.test.tsx`
Expected: PASS with no edits to either test file. A failure here means the extraction changed behaviour — fix `ReportRunPanel`, not the test.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/services/reports.ts frontend/src/features/reports/ReportRunPanel.tsx frontend/src/features/reports/GenerateSynopsisDialog.tsx
git commit -m "refactor(reports): extract the shared report run panel and add the build-plan client

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: The build-plan dialog and its paper picker

**Files:**
- Create: `frontend/src/features/reports/BuildPlanDialog.tsx`
- Create: `frontend/src/features/reports/BuildPlanDialog.test.tsx`

**Interfaces:**
- Consumes: `generateBuildPlan` (Task 7), `ReportRunPanel`, `POLL_TIMEOUT_MS`, `isReportTerminal`, `knowledgeState`-style asset filtering, `AssetRead`.
- Produces: `<BuildPlanDialog open onOpenChange projectId assets />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/reports/BuildPlanDialog.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BuildPlanDialog } from "@/features/reports/BuildPlanDialog";
import { aiProfile, makeAsset, makeReport, makeStep } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as reportsService from "@/services/reports";

vi.mock("@/services/reports");

const processedOne = makeAsset({ id: "a1", title: "First paper.pdf" });
const processedTwo = makeAsset({ id: "a2", title: "Second paper.pdf" });
const unprocessed = makeAsset({
  id: "a3",
  title: "Still processing.pdf",
  processing_status: "running",
  ai_profile: aiProfile({ status: "processing", embedding_status: "pending" }),
});

function render(assets = [processedOne, processedTwo, unprocessed]) {
  return renderWithProviders(
    <BuildPlanDialog open onOpenChange={vi.fn()} projectId="p1" assets={assets} />,
  );
}

describe("BuildPlanDialog — paper picker", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists only processed documents, all ticked by default", () => {
    render();

    const first = screen.getByRole("checkbox", { name: /first paper\.pdf/i });
    const second = screen.getByRole("checkbox", { name: /second paper\.pdf/i });
    expect(first).toBeChecked();
    expect(second).toBeChecked();
    // An unprocessed document cannot be built from, so it is not offered.
    expect(screen.queryByRole("checkbox", { name: /still processing/i })).not.toBeInTheDocument();
  });

  it("sends every ticked paper and starts the run", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "r1", title: "Build Plan", processing_status: "running" }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    await waitFor(() =>
      expect(reportsService.generateBuildPlan).toHaveBeenCalledWith("p1", ["a1", "a2"]),
    );
  });

  it("sends only the papers still ticked after one is unticked", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "r1", processing_status: "running" }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("checkbox", { name: /first paper\.pdf/i }));
    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    await waitFor(() =>
      expect(reportsService.generateBuildPlan).toHaveBeenCalledWith("p1", ["a2"]),
    );
  });

  it("blocks an empty selection", async () => {
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("checkbox", { name: /first paper\.pdf/i }));
    await user.click(screen.getByRole("checkbox", { name: /second paper\.pdf/i }));

    expect(screen.getByRole("button", { name: /get build plan/i })).toBeDisabled();
    expect(reportsService.generateBuildPlan).not.toHaveBeenCalled();
  });

  it("explains itself when the project has no processed documents", () => {
    render([unprocessed]);

    expect(screen.getByText(/no processed documents/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /get build plan/i })).toBeDisabled();
  });
});

describe("BuildPlanDialog — run states", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows progress and the pipeline while running", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "r1",
        processing_status: "running",
        steps: [makeStep({ node_name: "extract", title: "Extract what the paper builds" })],
      }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/generating your report/i);
    expect(await screen.findByText("Extract what the paper builds")).toBeInTheDocument();
  });

  it("offers a retry when generation failed, and re-runs it", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "r1",
        processing_status: "failed",
        processing_error: "Report generation failed (RuntimeError).",
      }),
    );
    vi.mocked(reportsService.retryReport).mockResolvedValue({ asset_id: "r1", status: "pending" });
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/RuntimeError/);
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(reportsService.retryReport).toHaveBeenCalledWith("r1"));
  });

  it("offers both downloads once complete", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "r1", title: "Build Plan", processing_status: "completed" }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(await screen.findByRole("button", { name: "Download DOCX" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download PDF" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/features/reports/BuildPlanDialog.test.tsx`
Expected: FAIL — cannot resolve `@/features/reports/BuildPlanDialog`.

- [ ] **Step 3: Write `BuildPlanDialog.tsx`**

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  POLL_TIMEOUT_MS,
  ReportRunPanel,
  isReportTerminal,
} from "@/features/reports/ReportRunPanel";
import { usePolling } from "@/hooks/usePolling";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];

/** The papers a build plan can actually be built from: this project's
 * own uploaded documents that finished processing. Mirrors the
 * backend's `ReportRepository.list_processed_documents` filter, so the
 * picker never offers something the endpoint would reject with a 422. */
function buildablePapers(assets: AssetRead[]): AssetRead[] {
  return assets.filter(
    (asset) => asset.asset_type === "document" && asset.processing_status === "completed",
  );
}

export function BuildPlanDialog({
  open,
  onOpenChange,
  projectId,
  assets,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  assets: AssetRead[];
}) {
  const papers = buildablePapers(assets);
  // All ticked by default (spec section 6): the common case is "build
  // from everything I uploaded", and unticking is cheaper than ticking.
  // `undefined` means "not touched yet", so a late-arriving asset list
  // does not silently leave a paper unticked.
  const [deselected, setDeselected] = useState<Set<string>>(new Set());
  const [assetId, setAssetId] = useState<string | null>(null);

  const selectedIds = papers.map((paper) => paper.id).filter((id) => !deselected.has(id));

  const generateMutation = useMutation({
    mutationFn: (ids: string[]) => reportsService.generateBuildPlan(projectId, ids),
    onSuccess: (accepted) => setAssetId(accepted.asset_id),
  });

  const queryClient = useQueryClient();
  const retryMutation = useMutation({
    mutationFn: (id: string) => reportsService.retryReport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", "report", assetId] }),
  });

  const reportQuery = usePolling({
    queryKey: ["reports", "report", assetId],
    queryFn: () => reportsService.getReport(assetId as string),
    isTerminal: isReportTerminal,
    enabled: assetId !== null,
    timeoutMs: POLL_TIMEOUT_MS,
  });

  function toggle(id: string) {
    setDeselected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      setDeselected(new Set());
      setAssetId(null);
      generateMutation.reset();
      retryMutation.reset();
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Get build plan</DialogTitle>
          <DialogDescription>
            Pick the papers you want to turn into a real project. AIKDAP reads what has to be
            built, researches further, and recommends tools and a step-by-step process.
          </DialogDescription>
        </DialogHeader>

        {!assetId && (
          <div className="flex max-h-64 flex-col gap-2 overflow-y-auto">
            {papers.length === 0 && (
              <p className="text-sm text-muted-foreground">
                This project has no processed documents yet. Upload a paper and wait for
                processing to finish.
              </p>
            )}
            {papers.map((paper) => (
              <label
                key={paper.id}
                className="flex cursor-pointer items-center gap-3 rounded-md border border-border p-3"
              >
                <input
                  type="checkbox"
                  checked={!deselected.has(paper.id)}
                  onChange={() => toggle(paper.id)}
                  className="h-4 w-4"
                />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{paper.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {paper.file_name}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}

        {generateMutation.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not start the build plan. Check your selection and try again.
          </p>
        )}

        <ReportRunPanel
          report={reportQuery.data}
          timedOut={reportQuery.timedOut}
          onRetry={(id) => retryMutation.mutate(id)}
          retrying={retryMutation.isPending}
          retryError={retryMutation.isError ? retryMutation.error : null}
        />

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Close
          </Button>
          {!assetId && (
            <Button
              // At least one paper is required (spec section 6); the
              // endpoint enforces it too, but the button should never
              // send a request that is guaranteed to 422.
              disabled={selectedIds.length === 0 || generateMutation.isPending}
              onClick={() => generateMutation.mutate(selectedIds)}
            >
              {generateMutation.isPending ? "Starting…" : "Get build plan"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/features/reports/BuildPlanDialog.test.tsx`
Expected: PASS — all eight tests.

If `makeReport` or `makeStep` does not accept an override used here, extend `frontend/src/test/fixtures.ts` rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/reports/BuildPlanDialog.tsx frontend/src/features/reports/BuildPlanDialog.test.tsx frontend/src/test/fixtures.ts
git commit -m "feat(reports): add the build-plan dialog with a paper picker

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: The builder-only button in `ProjectHeader`

**Files:**
- Modify: `frontend/src/features/projects/ProjectHeader.tsx`
- Test: `frontend/src/features/projects/ProjectHeader.test.tsx`

**Interfaces:**
- Consumes: `BuildPlanDialog` (Task 8), `ProjectRead.effective_persona`.
- Produces: no new exports — a button and a dialog inside the existing header.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/features/projects/ProjectHeader.test.tsx`:

```tsx
describe("ProjectHeader — Get build plan", () => {
  afterEach(() => vi.restoreAllMocks());

  it.each(["researcher", "student"] as const)(
    "does not show the button for the %s persona",
    (persona) => {
      renderWithProviders(
        <ProjectHeader
          project={makeProject({ effective_persona: persona })}
          assets={[]}
          runs={[]}
          onRequestUpload={vi.fn()}
        />,
      );
      expect(screen.queryByRole("button", { name: /get build plan/i })).not.toBeInTheDocument();
    },
  );

  it("shows the button for the builder persona", () => {
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "builder" })}
        assets={[]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /get build plan/i })).toBeInTheDocument();
  });

  it("opens the build-plan dialog with the project's papers", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "builder" })}
        assets={[makeAsset({ id: "a1", title: "First paper.pdf" })]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(screen.getByRole("checkbox", { name: /first paper\.pdf/i })).toBeChecked();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/features/projects/ProjectHeader.test.tsx`
Expected: FAIL — "Get build plan" button not found.

- [ ] **Step 3: Add the button and dialog**

In `frontend/src/features/projects/ProjectHeader.tsx`:

Add the import next to the synopsis dialog's:

```tsx
import { BuildPlanDialog } from "@/features/reports/BuildPlanDialog";
```

Add `Hammer` to the existing `lucide-react` import:

```tsx
import { AlertTriangle, FileText, FolderKanban, Hammer, Search, Trash2, X } from "lucide-react";
```

Add the state next to `synopsisOpen`:

```tsx
  const [buildPlanOpen, setBuildPlanOpen] = useState(false);
```

Add the button immediately after the student-only synopsis button:

```tsx
          {project.effective_persona === "builder" && (
            <Button variant="outline" onClick={() => setBuildPlanOpen(true)}>
              <Hammer className="h-4 w-4" />
              Get build plan
            </Button>
          )}
```

Add the dialog next to the synopsis dialog at the end of the component:

```tsx
      <BuildPlanDialog
        open={buildPlanOpen}
        onOpenChange={setBuildPlanOpen}
        projectId={project.id}
        assets={assets}
      />
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/features/projects/ProjectHeader.test.tsx`
Expected: PASS — the three new build-plan cases plus every existing header test.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/projects/ProjectHeader.tsx frontend/src/features/projects/ProjectHeader.test.tsx
git commit -m "feat(projects): show the Get build plan action for the builder persona

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Full-suite verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the whole backend suite**

Run: `docker exec aikdap_backend python -m pytest -q`
Expected: PASS except the known environmental failures — `test_execution_*` (no Docker inside the container), `test_cross_paper`, `test_fallback_grounding` (live LLM providers). Any other failure is a regression from this branch and must be fixed before finishing.

- [ ] **Step 2: Run the whole frontend suite**

```bash
cd frontend && npx vitest run
```

Expected: PASS, no skips introduced by this branch.

- [ ] **Step 3: Typecheck and lint the frontend**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors. `src/types/api.ts` is generated — if it reports errors, regenerate it rather than editing it.

- [ ] **Step 4: Confirm no secret ever reaches a persisted error**

Run: `docker exec aikdap_backend python -m pytest tests/test_build_plan_research.py -v`
Expected: PASS — the two scrubbing assertions (`"test-key" not in payload`, `"api.openalex.org" not in payload`) are the guard for the "never log or persist keys or URLs carrying them" constraint.

- [ ] **Step 5: Report, do not merge or push**

Summarise: the full backend result, the full frontend result, every ruling from the Design Rulings section that was applied, and the finishing options from `superpowers:finishing-a-development-branch`. Do **not** merge and do **not** push.

---

## Self-Review

**Spec coverage (section 6, plus its rows in 7 and 8):**

| Spec requirement | Task |
|---|---|
| `POST .../reports/build-plan` with `{asset_ids}` | 5 |
| Asset type `REPORT` | 5 |
| Paper picker, all processed documents ticked by default | 8 |
| Node 1 — extract method/models/datasets/metrics/compute/limitations | 2 |
| Node 2 — OpenAlex follow-up methods; Tavily implementations/libraries/datasets with real links | 3 |
| Node 3 — tools grouped by the five stages, reason tied to the paper, alternatives | 4 |
| Node 4 — phases from baseline to product, steps, definition of done, risks | 4 |
| Out of scope: tasks-module conversion | not implemented anywhere (by design) |
| §7 `asset_ids` not owned / not in project → 404 | 5 |
| §7 no processed documents → 422 | 5 |
| §7 uncovered section wording | 1 (`UNCOVERED`), applied in 2, 3, 4 |
| §7 LLM failure mid-report → asset `failed`, no partial document | 6 (reuses the step-4 failure path; `_parse` raises in 2 and 4) |
| §7 provider down / no key → step recorded, report unaffected | 3 |
| §8 nodes against a fake LLM | 2, 3, 4 |
| §8 DOCX/PDF re-opened, headings asserted | 6 |
| §8 frontend persona-driven button visibility | 9 |
| Reuse: same export path, tracing, idempotency guard, retry endpoint | 6 (task dispatch only), 7 (`ReportRunPanel`) |

**Placeholder scan:** no `TBD`, no "add error handling", no "similar to Task N". Every code step carries the actual code. Task 1 Step 4's stub file is explicitly marked as a stub with its real import list given, and Task 2 Step 4 replaces it wholesale.

**Type consistency checks applied:**
- `BuildPlanDependencies` is defined once with five fields (Task 2) and constructed with those five fields in the tests of Tasks 2, 3, 4 and in `build_build_plan_dependencies`. Task 1's one-field version is explicitly superseded in Task 2 Step 4.
- `ReportStepTracker(..., registry=)` is added in Task 1 Step 7 and used in Task 6 Step 3.
- `list_processed_documents_by_ids(project_id, asset_ids)` is added in Task 2 Step 5 and called by `RepositorySelectedDocumentLister` (Task 2) and `generate_build_plan` (Task 5).
- `_section(title, content, citations)` is defined in Task 2 and used in Tasks 2, 3, 4.
- `strip_urls` (not `_strip_urls`) is the exported name, used in Tasks 2 and 4.
- `BUILD_PLAN_SECTION_TITLES` is indexed `[0]`–`[4]` across Tasks 2, 3, 4 in the order Task 1 defines.
- `POLL_TIMEOUT_MS` / `isReportTerminal` / `stepsByAttempt` move to `ReportRunPanel` (Task 7) and are imported by both dialogs (Tasks 7, 8).
- `generateBuildPlan(projectId, assetIds)` is defined in Task 7 Step 2 and called in Task 8.

**Both open questions were resolved during planning, not deferred to the executor:** `AssetRepository` exposes no project-wide listing method, so the selection membership check became `ReportRepository.list_project_asset_ids` (Task 2 Step 5, called in Task 5 Step 4); and `tests/test_reports_export.py` already uses `pypdf`, which Task 6's export test matches.
