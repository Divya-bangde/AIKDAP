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
#:
#: The second alternative catches bare domains (e.g. "github.com/...",
#: "pytorch.org/docs") that a model is likely to name when pointing at a
#: library or paper without a scheme or "www.". It is deliberately a
#: closed TLD denylist, NOT a generic `\.[a-z]{2,}` suffix match -- a
#: generic suffix match would also eat legitimate non-URL tokens that
#: happen to contain a dot, such as "ResNet-50.pt", "config.yaml", or
#: "model.safetensors". Extend the list only with real TLDs, never widen
#: it to a catch-all pattern.
_URL_PATTERN = re.compile(
    r"(?:https?://|www\.|doi:)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|ai|dev|edu|gov|co)\b\S*",
    re.IGNORECASE,
)

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
