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
