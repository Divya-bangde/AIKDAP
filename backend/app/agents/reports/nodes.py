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

from app.agents.reports.prompts import (
    CITATION_SYSTEM_PROMPT,
    SECTION_SYSTEM_PROMPT,
    render_citation_prompt,
    render_section_prompt,
)
from app.agents.reports.state import (
    ProcessedDocument,
    ReportState,
    SECTION_QUERIES,
    SECTION_TITLES,
    SectionEvidence,
    SectionResult,
)
from app.core.config.settings import settings
from app.core.llm.errors import LLMError
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


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Accept a raw state string or an already-parsed UUID."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


class CitationDraft(BaseModel):
    """The bibliographic fields read off one document's front matter.

    Every field defaults to empty and stays empty when the front matter
    does not state it -- a reference is only rendered in APA form once
    authors, year and title are all genuinely present, so a missing
    field degrades to the plain filename line instead of a guess.
    """

    authors: str = ""
    year: str = ""
    title: str = ""
    venue: str = ""


class SectionDraft(BaseModel):
    """The LLM's structured response for one section."""

    content: str
    cited_asset_ids: list[str] = []


# ---------------------------------------------------------------------------
# Abstraction points
# ---------------------------------------------------------------------------


class DocumentLister(ABC):
    """Contract for listing a project's processed documents.

    Accepts the raw `ReportState["project_id"]` string or a `uuid.UUID`,
    so a fake in tests can use any id shape and a database-backed
    implementation parses it itself."""

    @abstractmethod
    async def list_processed(self, project_id: str | uuid.UUID) -> list[ProcessedDocument]:
        """Return every processed document available to draw a report from."""


class SectionSearcher(ABC):
    """Contract for retrieving evidence for one report section.

    `owner_id`/`project_id` accept a raw state string or a `uuid.UUID`,
    for the same reason as `DocumentLister.list_processed`."""

    @abstractmethod
    async def search(
        self, *, owner_id: str | uuid.UUID, project_id: str | uuid.UUID, query: str, limit: int
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

    async def list_processed(self, project_id: str | uuid.UUID) -> list[ProcessedDocument]:
        assets = await self._repository.list_processed_documents(_as_uuid(project_id))
        front_matter = await self._repository.get_front_matter([asset.id for asset in assets])
        return [
            {**_document_from_asset(asset), "front_matter": front_matter.get(asset.id, "")}
            for asset in assets
        ]


class KnowledgeBaseSectionSearcher(SectionSearcher):
    """Retrieves section evidence through the knowledge base's two-stage
    search -- the same service `agents.planner.nodes.SemanticAssetRetriever`
    delegates to, so report retrieval and research retrieval never
    diverge into two ranking implementations."""

    def __init__(
        self, session: AsyncSession, *, knowledge_base: KnowledgeBaseService | None = None
    ) -> None:
        # `knowledge_base` is injectable so a database-backed test can
        # supply a fixed-vector embedding provider -- no model call.
        self._service = knowledge_base or KnowledgeBaseService(session)

    async def search(
        self, *, owner_id: str | uuid.UUID, project_id: str | uuid.UUID, query: str, limit: int
    ) -> list[SectionEvidence]:
        outcome = await self._service.two_stage_search(
            _as_uuid(owner_id), query=query, project_id=_as_uuid(project_id), top_k=limit
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
    return {
        "documents": documents,
        "step": {
            "summary": f"Collected {len(documents)} processed document(s).",
            "output": {"document_count": len(documents)},
        },
    }


async def retrieve_evidence_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Retrieve evidence for every section except References.

    Its own node, so retrieval has its own step in the trace (timing,
    failure) separate from the LLM writing that follows."""
    dependencies = _dependencies(config)
    documents = state.get("documents", [])
    document_lookup = {document["asset_id"]: document for document in documents}
    evidence_by_title: dict[str, list[SectionEvidence]] = {}

    for title in SECTION_TITLES[state["kind"]]:
        if title == "References":
            continue
        evidence = await dependencies.section_searcher.search(
            owner_id=state["owner_id"],
            project_id=state["project_id"],
            query=SECTION_QUERIES.get(title, title),
            limit=SECTION_EVIDENCE_LIMIT,
        )
        # Searchers don't have the project's document list, so fill in
        # the real title/file_name from the collected documents -- the
        # model then sees `[id] <real title> (<real file name>):`.
        evidence_by_title[title] = [
            {**item, "title": document_lookup[item["asset_id"]]["title"], "file_name": document_lookup[item["asset_id"]]["file_name"]}
            if item["asset_id"] in document_lookup
            else item
            for item in evidence
        ]

        if not evidence_by_title[title]:
            # Semantic search can return nothing for a section whose
            # query falls below the knowledge base's relevance gate --
            # "Methodology" on a paper that words it as "approach" is
            # the common case, and dropping the section entirely was
            # losing the paper's central contribution.
            #
            # Each document's AI-profile summary is still derived from
            # the user's own document, so falling back to it keeps the
            # grounding guarantee (spec section 7) intact: coarser
            # evidence, never invented evidence. A document with no
            # summary contributes nothing rather than an empty snippet.
            evidence_by_title[title] = [
                SectionEvidence(
                    asset_id=document["asset_id"],
                    title=document["title"],
                    file_name=document["file_name"],
                    snippet=document["summary"],
                )
                for document in documents
                if document["summary"]
            ]

    with_evidence = sum(1 for items in evidence_by_title.values() if items)
    excerpt_count = sum(len(items) for items in evidence_by_title.values())
    logger.info(
        "report_evidence_retrieved",
        report_id=state.get("report_id"),
        sections_with_evidence=with_evidence,
        section_count=len(evidence_by_title),
    )
    return {
        "evidence": evidence_by_title,
        "step": {
            "summary": f"Found evidence for {with_evidence} of {len(evidence_by_title)} section(s).",
            "output": {
                "section_count": len(evidence_by_title),
                "sections_with_evidence": with_evidence,
                "excerpt_count": excerpt_count,
            },
        },
    }


async def write_sections_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Write each section with one LLM call, from the evidence
    `retrieve_evidence_node` found -- or mark it uncovered without ever
    calling the model when there is none (spec section 7)."""
    dependencies = _dependencies(config)
    kind = state["kind"]
    documents = state.get("documents", [])
    evidence_by_title = state.get("evidence", {})
    results: list[SectionResult] = []
    llm_calls = 0

    for title in SECTION_TITLES[kind]:
        if title == "References":
            # Built deterministically in `coverage_check_node` from what
            # the other sections actually cited -- never retrieved or
            # sent to the model (spec section 7: never invent citations).
            results.append(SectionResult(title=title, content="", covered=False, citations=[]))
            continue

        evidence = evidence_by_title.get(title, [])
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
        llm_calls += 1
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

    written = [section for section in results if section["title"] != "References"]
    covered_count = sum(1 for section in written if section["covered"])
    logger.info(
        "report_sections_written",
        report_id=state.get("report_id"),
        covered=covered_count,
        total=len(written),
    )
    return {
        "sections": results,
        "step": {
            "summary": (
                f"Wrote {covered_count} of {len(written)} section(s); "
                f"{len(written) - covered_count} not covered by your documents."
            ),
            "output": {"covered": covered_count, "total": len(written), "llm_calls": llm_calls},
        },
    }


async def coverage_check_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Build the deterministic References section (if this kind has one)
    from every asset id any other section actually cited, and log the
    final coverage."""
    dependencies = _dependencies(config)
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
            await _reference_line(document_lookup[asset_id], dependencies.llm_gateway)
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

    covered_count = sum(1 for section in sections if section["covered"])
    reference_count = len(
        next((section["citations"] for section in sections if section["title"] == "References"), [])
    )
    logger.info(
        "report_coverage_checked",
        report_id=state.get("report_id"),
        covered=covered_count,
        total=len(sections),
    )
    return {
        "sections": sections,
        "step": {
            "summary": f"{covered_count} of {len(sections)} section(s) covered; {reference_count} document(s) referenced.",
            "output": {"covered": covered_count, "total": len(sections), "reference_count": reference_count},
        },
    }


async def _reference_line(document: ProcessedDocument, gateway: LLMGateway) -> str:
    """One References entry for `document`, in APA form when possible.

    Falls back to `<title> (<file name>)` -- the only form this section
    had before -- whenever the front matter is missing, the model call
    fails, or any of authors/year/title comes back empty. A reference is
    a citation: half-known bibliographic data must degrade to the
    filename, never to a plausible-looking invention (spec section 7).
    """
    fallback = f"{document['title']} ({document['file_name']})"
    front_matter = document.get("front_matter", "")
    if not front_matter.strip():
        return fallback

    try:
        response = await gateway.generate(
            prompt=render_citation_prompt(
                file_name=document["file_name"], front_matter=front_matter
            ),
            system_prompt=CITATION_SYSTEM_PROMPT,
            model=settings.synthesis_model,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "CitationDraft", "schema": CitationDraft.model_json_schema()},
            },
        )
        text = (response.content or "").strip()
        fenced = _JSON_FENCE.match(text)
        draft = CitationDraft.model_validate_json(fenced.group(1) if fenced else text)
    except (ValidationError, ValueError, LLMError) as exc:
        # Never fatal: a report whose body is written must not be lost
        # to a failed bibliographic lookup.
        logger.warning(
            "report_citation_extraction_failed",
            asset_id=document["asset_id"],
            error_type=type(exc).__name__,
        )
        return fallback

    authors, title = draft.authors.strip(), draft.title.strip()
    if not (authors and title):
        return fallback
    # APA's own notation for an undated source. Preprints routinely
    # print no year on the title page, and "n.d." is the correct way to
    # say so -- unlike authors or title, a missing year has a faithful
    # rendering rather than only a fallback.
    year = draft.year.strip() or "n.d."
    venue = draft.venue.strip()
    return f"{authors} ({year}). {title}." + (f" {venue}." if venue else "")


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
