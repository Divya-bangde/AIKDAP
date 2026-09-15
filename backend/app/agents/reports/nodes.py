"""Graph nodes for report generation, and the strategy abstractions
they execute against -- mirrors `agents.planner.nodes`'s shape:
abstractions first, then the nodes themselves, injected through the
graph config exactly like `GraphDependencies` there.
"""

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.reports.prompts import SECTION_SYSTEM_PROMPT, render_section_prompt
from app.agents.reports.state import (
    ProcessedDocument,
    ReportState,
    SECTION_QUERIES,
    SECTION_TITLES,
    SectionEvidence,
    SectionResult,
)
from app.core.config.settings import settings
from app.core.llm.gateway import LLMGateway, get_llm_gateway
from app.core.logging.logger import get_logger
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.models import Asset
from app.modules.knowledge_base.service import KnowledgeBaseService
from app.modules.reports.repository import ReportRepository

logger = get_logger(__name__)

#: How many evidence excerpts to retrieve per section.
SECTION_EVIDENCE_LIMIT = 5

#: Strips a ```json ... ``` fence, the same tolerance
#: `paper_suggestion._parse_gaps` applies.
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class SectionDraft(BaseModel):
    """The LLM's structured response for one section."""

    content: str
    cited_asset_ids: list[str] = []


# ---------------------------------------------------------------------------
# Abstraction points
# ---------------------------------------------------------------------------


class DocumentLister(ABC):
    """Contract for listing a project's processed documents.

    `project_id` is the raw `ReportState["project_id"]` string -- kept
    opaque at this boundary (rather than a parsed `uuid.UUID`) so a fake
    in tests can use any id shape; a database-backed implementation
    parses it into a `uuid.UUID` itself before querying.
    """

    @abstractmethod
    async def list_processed(self, project_id: str) -> list[ProcessedDocument]:
        """Return every processed document available to draw a report from."""


class SectionSearcher(ABC):
    """Contract for retrieving evidence for one report section.

    `owner_id`/`project_id` are the raw state strings, for the same
    reason as `DocumentLister.list_processed`.
    """

    @abstractmethod
    async def search(
        self, *, owner_id: str, project_id: str, query: str, limit: int
    ) -> list[SectionEvidence]:
        """Return the best-matching excerpts for `query`, owner-scoped."""


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _document_from_asset(asset: Asset) -> ProcessedDocument:
    profile = AIProfile.model_validate(asset.ai_profile or {})
    return ProcessedDocument(
        asset_id=str(asset.id),
        title=asset.title,
        file_name=asset.file_name,
        summary=profile.summary or "",
        topics=list(profile.topics or []),
    )


class RepositoryDocumentLister(DocumentLister):
    """Lists processed documents through `ReportRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = ReportRepository(session)

    async def list_processed(self, project_id: str) -> list[ProcessedDocument]:
        assets = await self._repository.list_processed_documents(uuid.UUID(project_id))
        return [_document_from_asset(asset) for asset in assets]


class KnowledgeBaseSectionSearcher(SectionSearcher):
    """Retrieves section evidence through the knowledge base's two-stage
    search -- the same service `agents.planner.nodes.SemanticAssetRetriever`
    delegates to, so report retrieval and research retrieval never
    diverge into two ranking implementations."""

    def __init__(self, session: AsyncSession) -> None:
        self._service = KnowledgeBaseService(session)

    async def search(
        self, *, owner_id: str, project_id: str, query: str, limit: int
    ) -> list[SectionEvidence]:
        outcome = await self._service.two_stage_search(
            uuid.UUID(owner_id), query=query, project_id=uuid.UUID(project_id), top_k=limit
        )
        evidence: list[SectionEvidence] = []
        for hit in outcome.hits:
            evidence.append(
                SectionEvidence(
                    asset_id=str(hit.chunk.asset_id),
                    title=query,
                    file_name="",
                    snippet=hit.chunk.content,
                )
            )
        return evidence


@dataclass(frozen=True)
class ReportGraphDependencies:
    """The strategies one report-generation run executes against,
    injected through `config["configurable"]["dependencies"]` -- the
    same pattern `agents.planner.nodes.GraphDependencies` uses."""

    document_lister: DocumentLister
    section_searcher: SectionSearcher
    llm_gateway: LLMGateway


def build_report_dependencies(session: AsyncSession) -> ReportGraphDependencies:
    """Assemble the default dependency set for a database-backed run."""
    return ReportGraphDependencies(
        document_lister=RepositoryDocumentLister(session),
        section_searcher=KnowledgeBaseSectionSearcher(session),
        llm_gateway=get_llm_gateway(),
    )


def _dependencies(config: RunnableConfig) -> ReportGraphDependencies:
    dependencies = (config.get("configurable") or {}).get("dependencies")
    if not isinstance(dependencies, ReportGraphDependencies):
        raise RuntimeError(
            "Report graph invoked without a ReportGraphDependencies instance in "
            "config['configurable']['dependencies']."
        )
    return dependencies


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def collect_documents_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Collect every processed document's AI-profile summary and topics."""
    dependencies = _dependencies(config)
    documents = await dependencies.document_lister.list_processed(state["project_id"])
    logger.info("report_documents_collected", report_id=state.get("report_id"), document_count=len(documents))
    return {"documents": documents}


async def write_sections_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """For each section: retrieve evidence, then write it with one LLM
    call -- or mark it uncovered without ever calling the model, when
    retrieval found nothing (spec section 7)."""
    dependencies = _dependencies(config)
    kind = state["kind"]
    documents = state.get("documents", [])
    document_lookup = {document["asset_id"]: document for document in documents}
    results: list[SectionResult] = []

    for title in SECTION_TITLES[kind]:
        if title == "References":
            # Built deterministically in `coverage_check_node` from what
            # the other sections actually cited -- never retrieved or
            # sent to the model (spec section 7: never invent citations).
            results.append(SectionResult(title=title, content="", covered=False, citations=[]))
            continue

        query = SECTION_QUERIES.get(title, title)
        evidence = await dependencies.section_searcher.search(
            owner_id=state["owner_id"],
            project_id=state["project_id"],
            query=query,
            limit=SECTION_EVIDENCE_LIMIT,
        )
        # `SectionSearcher.search` implementations (e.g.
        # `KnowledgeBaseSectionSearcher`) don't have the project's
        # document list to draw a real title/file_name from -- fill
        # them in here from the state's own collected documents so the
        # model sees `[id] <real title> (<real file name>):` instead of
        # the search query standing in for both.
        evidence = [
            {**item, "title": document_lookup[item["asset_id"]]["title"], "file_name": document_lookup[item["asset_id"]]["file_name"]}
            if item["asset_id"] in document_lookup
            else item
            for item in evidence
        ]
        if not evidence:
            results.append(
                SectionResult(title=title, content="Not covered by your documents.", covered=False, citations=[])
            )
            continue

        prompt = render_section_prompt(title=title, kind=kind, evidence=evidence, documents=documents)
        response = await dependencies.llm_gateway.generate(
            prompt=prompt,
            system_prompt=SECTION_SYSTEM_PROMPT,
            model=settings.synthesis_model,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "SectionDraft", "schema": SectionDraft.model_json_schema()},
            },
        )
        draft = _parse_section(response.content, title)

        valid_asset_ids = {item["asset_id"] for item in evidence}
        citations = [asset_id for asset_id in draft.cited_asset_ids if asset_id in valid_asset_ids]
        content = draft.content.strip() or "Not covered by your documents."
        covered = content != "Not covered by your documents."
        results.append(
            SectionResult(
                title=title,
                content=content,
                covered=covered,
                # A section marked not-covered must never carry
                # citations forward into References -- an empty draft
                # earned no evidence credit even if the model happened
                # to name asset ids before going empty.
                citations=citations if covered else [],
            )
        )

    logger.info(
        "report_sections_written",
        report_id=state.get("report_id"),
        covered=sum(1 for section in results if section["covered"]),
        total=len(results),
    )
    return {"sections": results}


async def coverage_check_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Build the deterministic References section (if this kind has one)
    from every asset id any other section actually cited, and log the
    final coverage."""
    sections = state.get("sections", [])
    documents = state.get("documents", [])
    document_lookup = {document["asset_id"]: document for document in documents}

    cited_asset_ids: list[str] = []
    for section in sections:
        if section["title"] == "References":
            continue
        for asset_id in section["citations"]:
            if asset_id not in cited_asset_ids:
                cited_asset_ids.append(asset_id)

    if any(section["title"] == "References" for section in sections):
        lines = [
            f"{document_lookup[asset_id]['title']} ({document_lookup[asset_id]['file_name']})"
            for asset_id in cited_asset_ids
            if asset_id in document_lookup
        ]
        references_content = "\n".join(lines) if lines else "Not covered by your documents."
        # `covered` (and which ids survive into `citations`) must track
        # the reference lines actually built, not the raw cited-id list
        # -- a cited id missing from `document_lookup` produces no line
        # and must not be reported as covered.
        resolved_asset_ids = [asset_id for asset_id in cited_asset_ids if asset_id in document_lookup]

        sections = [
            SectionResult(
                title=section["title"],
                content=references_content,
                covered=bool(lines),
                citations=resolved_asset_ids,
            )
            if section["title"] == "References"
            else section
            for section in sections
        ]

    logger.info(
        "report_coverage_checked",
        report_id=state.get("report_id"),
        covered=sum(1 for section in sections if section["covered"]),
        total=len(sections),
    )
    return {"sections": sections}


def _parse_section(content: str, title: str) -> SectionDraft:
    """Parse the model's structured section response.

    Unlike `paper_suggestion._parse_gaps` (which degrades to "no gaps"
    on unusable output, because that step is non-critical), every
    report node is critical -- an unparseable response here must raise,
    so the whole run fails loudly rather than silently keeping a
    plausible-looking but ungrounded section (spec section 7: "LLM
    failure mid-report" marks the whole asset failed).
    """
    match = _JSON_FENCE.match(content)
    text = match.group(1) if match else content
    try:
        return SectionDraft.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise RuntimeError(f"Could not parse the '{title}' section response.") from exc
