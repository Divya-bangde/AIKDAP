"""Versioned, source-backed inputs for comparison; never trusts legacy metadata."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.agents.planner.analysis import SYSTEM_PROMPT, analyze_research_document
from app.core.config import settings
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.research.models import DocumentUnderstandingCache
from app.modules.research.schemas import (
    ComparisonSourceVersion,
    DocumentPassage,
    ResearchDocumentUnderstanding,
    SufficiencyStatus,
)

EXTRACTOR_VERSION = "research-understanding-v2"
PROMPT_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
# An explicit bound returns insufficiency instead of silently truncating a paper.
MAX_DOCUMENT_CHARACTERS = 120_000


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def load_source(session, asset):
    result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.asset_id == asset.id,
            KnowledgeChunk.project_id == asset.project_id,
        ).order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
    )
    return list(result.scalars().all())


def source_version(asset, chunks) -> ComparisonSourceVersion:
    return ComparisonSourceVersion(
        asset_id=asset.id,
        checksum=asset.checksum,
        asset_version=asset.version,
        chunk_fingerprint=_digest([
            (c.id, c.chunk_index, c.page_number, c.content) for c in chunks
        ]),
        extractor_version=EXTRACTOR_VERSION,
        model_identity=settings.default_llm,
        prompt_version=PROMPT_VERSION,
    )


def understanding_cache_key(asset, version, request) -> str:
    return _digest({
        "source": version.model_dump(mode="json"),
        "title": asset.title,
        "request": request.model_dump(mode="json"),
        # Changing the local model endpoint invalidates its cached identity too.
        "endpoint": settings.ollama_base_url,
        "think": settings.qwen_think,
    })


async def understand_source(session, asset, chunks, request):
    """Caller must authorize every input before calling this function."""
    version = source_version(asset, chunks)
    key = understanding_cache_key(asset, version, request)
    cached = await session.scalar(select(DocumentUnderstandingCache).where(
        DocumentUnderstandingCache.asset_id == asset.id,
        DocumentUnderstandingCache.cache_key == key,
    ))
    if cached is not None:
        return ResearchDocumentUnderstanding.model_validate(cached.understanding), version

    if not chunks or sum(len(c.content) for c in chunks) > MAX_DOCUMENT_CHARACTERS:
        understanding = ResearchDocumentUnderstanding(
            title=asset.title, sufficiency=SufficiencyStatus.INSUFFICIENT,
            sufficiency_reason=("No extracted passages are available." if not chunks else
                                "Document exceeds the bounded comparison input size; full evidence was not analyzed."),
        )
    else:
        understanding = await analyze_research_document(
            asset.id, asset.project_id, request, session,
            source_chunks=chunks, persist_metadata=False, allow_fallback=False,
        )
        understanding.source_passages = [DocumentPassage(
            asset_id=asset.id, chunk_id=c.id, chunk_index=c.chunk_index,
            page=c.page_number, content=c.content,
        ) for c in chunks]
    understanding.asset_id = asset.id
    # The library title is authoritative metadata, never invented by the reducer.
    understanding.title = asset.title
    await session.execute(insert(DocumentUnderstandingCache).values(
        asset_id=asset.id, cache_key=key,
        source_version=version.model_dump(mode="json"),
        understanding=understanding.model_dump(mode="json"),
    ).on_conflict_do_nothing(index_elements=["cache_key"]))
    return understanding, version
