"""Resolve a PDF document to its OpenAlex work (paper map, Phase 1c).

Order of evidence, strongest first, and never a guess:

1. an OpenAlex id already known (the paper was imported from an
   OpenAlex suggestion via Add & Re-run);
2. a DOI printed on the first two pages -> `GET /works/doi:{doi}`;
3. a title search, accepted only when a result's normalized title is
   >= `TITLE_MATCH_THRESHOLD` similar to the document's title.

Anything else is `not_found`. A lookup that cannot reach OpenAlex leaves
the row `pending` and raises, so the Celery task retries it.
"""

import io
import re
import uuid
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging.logger import get_logger
from app.modules.assets.enums import AssetSource
from app.modules.assets.models import Asset
from app.modules.assets.storage import StorageProvider
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.papers.models import OpenAlexStatus, PaperReference
from app.modules.papers.openalex import (
    OpenAlexClient,
    OpenAlexLookupError,
    extract_doi,
    parse_work,
)
from app.modules.papers.repository import PaperReferenceRepository
from app.modules.papers.schemas import PaperGraph, PaperLink, PaperNode, UnmatchedPaper
from app.modules.projects.repository import ProjectRepository

logger = get_logger(__name__)

TITLE_MATCH_THRESHOLD = 0.9
PDF_MIME = "application/pdf"


def normalize_title(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def best_title_match(
    candidates: list[str], results: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The search result whose title clears the threshold, if any."""
    best: tuple[float, dict[str, Any]] | None = None
    for result in results:
        title = result.get("display_name") or result.get("title")
        if not title:
            continue
        for candidate in candidates:
            score = title_similarity(candidate, title)
            if score >= TITLE_MATCH_THRESHOLD and (best is None or score > best[0]):
                best = (score, result)
    return best[1] if best else None


_IMPORTED_FILE_RE = re.compile(r"^(W\d+)\.pdf$")


def imported_openalex_id(asset: Asset) -> str | None:
    """The OpenAlex id of a paper added via Add & Re-run.

    `workers.tasks._import_paper` names an imported file after its
    OpenAlex work (`W123.pdf`), so the id is recoverable from the asset
    alone -- for new imports and existing ones alike.
    """
    if asset.source is not AssetSource.IMPORTED:
        return None
    match = _IMPORTED_FILE_RE.match(asset.file_name or "")
    return match.group(1) if match else None


def _pdf_metadata_title(content: bytes) -> str | None:
    try:
        from pypdf import PdfReader

        title = (PdfReader(io.BytesIO(content)).metadata or {}).get("/Title")
    except Exception:  # noqa: BLE001 - metadata is optional
        return None
    return str(title).strip() if title else None


class PaperReferenceService:
    def __init__(
        self,
        session: AsyncSession,
        storage: StorageProvider,
        *,
        client: OpenAlexClient | None = None,
    ) -> None:
        self._session = session
        self._storage = storage
        self._references = PaperReferenceRepository(session)
        self._client = client or OpenAlexClient()

    async def lookup(self, asset_id: uuid.UUID) -> PaperReference | None:
        """Match one PDF asset to OpenAlex. `None` for a non-PDF asset."""
        asset = await self._session.get(Asset, asset_id)
        if asset is None or asset.mime_type != PDF_MIME:
            return None
        reference = await self._references.get_or_create(asset.id, asset.project_id)
        if reference.openalex_status == OpenAlexStatus.MATCHED.value:
            return reference

        if not reference.openalex_id:
            reference.openalex_id = imported_openalex_id(asset)

        work: dict[str, Any] | None = None
        if reference.openalex_id:
            work = await self._client.get_by_id(reference.openalex_id)
        if work is None:
            doi = extract_doi(await self._first_pages_text(asset.id))
            if doi:
                reference.doi = doi
                work = await self._client.get_by_doi(doi)
        if work is None:
            candidates = await self._title_candidates(asset)
            for candidate in candidates:
                work = best_title_match(candidates, await self._client.search_title(candidate))
                if work is not None:
                    break

        if work is None:
            reference.openalex_status = OpenAlexStatus.NOT_FOUND.value
        else:
            reference.apply_work(parse_work(work))
        await self._session.commit()
        logger.info(
            "paper_reference_lookup",
            asset_id=str(asset_id),
            status=reference.openalex_status,
            openalex_id=reference.openalex_id,
        )
        return reference

    async def _first_pages_text(self, asset_id: uuid.UUID) -> str:
        result = await self._session.execute(
            select(KnowledgeChunk.content)
            .where(KnowledgeChunk.asset_id == asset_id, KnowledgeChunk.page_number <= 2)
            .order_by(KnowledgeChunk.chunk_index)
        )
        return "\n".join(result.scalars().all())

    async def _title_candidates(self, asset: Asset) -> list[str]:
        candidates: list[str] = []
        try:
            metadata_title = _pdf_metadata_title(await self._storage.read(asset.storage_path))
        except Exception:  # noqa: BLE001 - the file title is a fallback only
            metadata_title = None
        for title in (metadata_title, re.sub(r"\.pdf$", "", asset.title, flags=re.I)):
            # Too short to identify a paper ("Draft", "paper1").
            if title and len(normalize_title(title).split()) >= 3 and title not in candidates:
                candidates.append(title)
        return candidates


# ---------------------------------------------------------------------------
# Project paper map (Phase 4)
# ---------------------------------------------------------------------------

#: At most this many outside works are added to the graph...
MAX_EXTERNAL_NODES = 15
#: ...and only works cited by at least this many project papers.
MIN_EXTERNAL_CITERS = 2

ProjectPaper = tuple[Asset, PaperReference | None]


class ProjectNotFoundError(Exception):
    """The project does not exist or is not the caller's."""


class OutsidePaperNotFoundError(Exception):
    """OpenAlex has no work with this id."""


class OutsidePaperNotOpenAccessError(Exception):
    """The work has no open-access PDF, so it cannot be imported."""


def _matched(papers: list[ProjectPaper]) -> dict[str, tuple[Asset, PaperReference]]:
    """Matched project papers by OpenAlex id. The first upload wins when
    the same work was added twice."""
    matched: dict[str, tuple[Asset, PaperReference]] = {}
    for asset, reference in papers:
        if (
            reference is not None
            and reference.openalex_status == OpenAlexStatus.MATCHED.value
            and reference.openalex_id
        ):
            matched.setdefault(reference.openalex_id, (asset, reference))
    return matched


def frequent_external_ids(papers: list[ProjectPaper]) -> list[str]:
    """Outside works cited by >= `MIN_EXTERNAL_CITERS` project papers,
    most-cited first, at most `MAX_EXTERNAL_NODES`."""
    matched = _matched(papers)
    counts: dict[str, int] = {}
    for _, reference in matched.values():
        for work_id in set(reference.referenced_works or []):
            if work_id not in matched:
                counts[work_id] = counts.get(work_id, 0) + 1
    frequent = [work_id for work_id, count in counts.items() if count >= MIN_EXTERNAL_CITERS]
    frequent.sort(key=lambda work_id: (-counts[work_id], work_id))
    return frequent[:MAX_EXTERNAL_NODES]


def build_paper_graph(
    papers: list[ProjectPaper], external_works: list[dict[str, Any]] | None = None
) -> PaperGraph:
    """Nodes, citation links and the unmatched list for one project.

    A link means project paper A's OpenAlex `referenced_works` contains
    B, where B is another matched project paper or one of
    `external_works` (already parsed with `openalex.parse_work`).
    """
    matched = _matched(papers)
    nodes = [
        PaperNode(
            id=work_id,
            title=reference.title or asset.title,
            year=reference.publication_year,
            cited_by_count=reference.cited_by_count,
            kind="suggested" if asset.source is AssetSource.IMPORTED else "uploaded",
            in_project=True,
            doi=reference.doi,
            asset_id=asset.id,
        )
        for work_id, (asset, reference) in matched.items()
    ]
    for work in external_works or []:
        if work["openalex_id"] and work["openalex_id"] not in matched:
            nodes.append(
                PaperNode(
                    id=work["openalex_id"],
                    title=work["title"] or work["openalex_id"],
                    year=work["publication_year"],
                    cited_by_count=work["cited_by_count"],
                    kind="external",
                    in_project=False,
                    doi=work["doi"],
                )
            )

    node_ids = {node.id for node in nodes}
    links = [
        PaperLink(source=work_id, target=cited)
        for work_id, (_, reference) in matched.items()
        for cited in sorted(set(reference.referenced_works or []))
        if cited in node_ids and cited != work_id
    ]
    unmatched = [
        UnmatchedPaper(
            paper_id=asset.id,
            title=asset.title,
            status=reference.openalex_status if reference else OpenAlexStatus.PENDING.value,
        )
        for asset, reference in papers
        if reference is None or reference.openalex_status != OpenAlexStatus.MATCHED.value
    ]
    return PaperGraph(nodes=nodes, links=links, unmatched=unmatched)


class PaperGraphService:
    """The project citation graph, and adding an outside work from it."""

    def __init__(self, session: AsyncSession, *, client: OpenAlexClient | None = None) -> None:
        self._references = PaperReferenceRepository(session)
        self._projects = ProjectRepository(session)
        self._client = client or OpenAlexClient()

    async def _ensure_project_owned(self, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self._projects.get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectNotFoundError(project_id)

    async def get_graph(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, *, include_external: bool
    ) -> PaperGraph:
        """The graph; outside works are one batched, cached OpenAlex
        lookup, and an unreachable OpenAlex degrades to project papers
        only rather than failing the request."""
        await self._ensure_project_owned(owner_id, project_id)
        papers = await self._references.list_project_pdfs(project_id)

        works: list[dict[str, Any]] = []
        unavailable = False
        external_ids = frequent_external_ids(papers) if include_external else []
        if external_ids:
            try:
                works = [parse_work(item) for item in await self._client.get_many(external_ids)]
            except OpenAlexLookupError:
                logger.warning("paper_graph_external_lookup_failed", project_id=str(project_id))
                unavailable = True

        graph = build_paper_graph(papers, works)
        graph.external_unavailable = unavailable
        return graph

    async def import_outside_paper(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, openalex_id: str
    ) -> None:
        """Queue one outside work's open-access PDF for import.

        Uses the same download -> imported asset -> processing steps as
        Add & Re-run (`workers.tasks.add_open_access_paper`), minus the
        re-run: there is no research run behind a graph node. The asset
        is named `W123.pdf`, so its OpenAlex match is exact and it joins
        the graph as a project paper once processed.
        """
        await self._ensure_project_owned(owner_id, project_id)
        work = await self._client.get_by_id(openalex_id)
        if work is None:
            raise OutsidePaperNotFoundError(openalex_id)
        pdf_url = (work.get("best_oa_location") or {}).get("pdf_url")
        if not pdf_url:
            raise OutsidePaperNotOpenAccessError(openalex_id)

        # Local import: `workers.tasks` imports this module at load time.
        from app.workers.tasks import import_project_paper

        import_project_paper.delay(
            str(project_id), str(owner_id), openalex_id, pdf_url, parse_work(work)["title"]
        )
