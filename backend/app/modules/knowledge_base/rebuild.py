"""Resumable re-embedding with short claims and atomic project activation.

Model calls happen outside database transactions. Retrieval sees only the
previous activated space until every current chunk has a validated vector.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.assets.enums import EmbeddingStatus
from app.modules.knowledge_base.embeddings import get_embedding_provider
from app.modules.knowledge_base.models import EmbeddingRebuild, EmbeddingRebuildItem, KnowledgeChunk
from app.modules.knowledge_base.spaces import validate_vectors
from app.modules.projects.models import Project


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def lock_project(session, project_id):
    return await session.scalar(select(Project).where(Project.id == project_id).with_for_update())


async def activate_initial_space(session, project_id, space_id):
    """Ingestion may initialize a fully verified new project, never bless legacy data."""
    project = await lock_project(session, project_id)
    if project is None or project.active_embedding_space_id is not None:
        return
    await session.flush()
    unverified = await session.scalar(select(func.count()).select_from(KnowledgeChunk).where(
        KnowledgeChunk.project_id == project_id,
        or_(KnowledgeChunk.embedding_space_id.is_(None), KnowledgeChunk.embedding_space_id != space_id),
    ))
    if not unverified:
        project.active_embedding_space_id = space_id


async def request_rebuild(session, project_id, provider):
    await session.execute(insert(EmbeddingRebuild).values(
        id=uuid.uuid4(), project_id=project_id, space_id=provider.space.id,
        space=provider.space.as_dict(), status="queued", total=0, completed=0, failed=0,
    ).on_conflict_do_nothing(index_elements=["project_id", "space_id"]))
    job = await session.scalar(select(EmbeddingRebuild).where(
        EmbeddingRebuild.project_id == project_id, EmbeddingRebuild.space_id == provider.space.id).with_for_update())
    # An active claim must not be reset by repeated UI clicks.
    if job.lease_until is None or job.lease_until <= datetime.now(timezone.utc):
        job.status = "queued"
        job.error = None
    await session.commit()
    return job


async def readiness(session, project_id, provider):
    # Workers may activate between this request's ownership check and status read.
    project = await session.scalar(select(Project).where(Project.id == project_id)
                                   .execution_options(populate_existing=True))
    space_id = provider.space.id
    total, searchable, legacy = (await session.execute(select(
        func.count(),
        func.count().filter(KnowledgeChunk.embedding_space_id == space_id,
                            KnowledgeChunk.embedding_status == EmbeddingStatus.COMPLETED,
                            KnowledgeChunk.embedding.is_not(None)),
        func.count().filter(KnowledgeChunk.embedding_space_id.is_(None)),
    ).select_from(KnowledgeChunk).where(KnowledgeChunk.project_id == project_id))).one()
    compatible = project.active_embedding_space_id == space_id
    searchable = searchable if compatible else 0
    job = await session.scalar(select(EmbeddingRebuild).where(
        EmbeddingRebuild.project_id == project_id, EmbeddingRebuild.space_id == space_id)
        .execution_options(populate_existing=True))
    return dict(project_id=str(project_id), configured_space=provider.space.as_dict(),
                active_space_id=project.active_embedding_space_id, total_chunks=total,
                searchable_chunks=searchable, legacy_chunks=legacy, compatible=compatible,
                ready=bool(total and searchable == total), rebuild=(
                    {key: str(getattr(job, key)) if key == "id" else getattr(job, key)
                     for key in ("id", "status", "total", "completed", "failed", "error")}
                    if job else None))


async def run_rebuild(session_factory, rebuild_id, *, provider=None, batch_size=32):
    provider = provider or get_embedding_provider()
    token = uuid.uuid4()
    # Claim lifetime exceeds a bounded provider call; expired claims are safely reclaimable.
    from app.core.config import settings
    lease_seconds = max(300, int(settings.embedding_timeout) + 120)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        job = await session.scalar(update(EmbeddingRebuild).where(
            EmbeddingRebuild.id == rebuild_id,
            EmbeddingRebuild.space_id == provider.space.id,
            EmbeddingRebuild.status != "completed",
            or_(EmbeddingRebuild.lease_until.is_(None), EmbeddingRebuild.lease_until < now),
        ).values(claim_token=token, lease_until=now + timedelta(seconds=lease_seconds),
                 status="running", error=None).returning(EmbeddingRebuild))
        await session.commit()
        if job is None:
            existing = await session.get(EmbeddingRebuild, rebuild_id)
            if existing is None:
                return {"status": "missing"}
            if existing.status == "completed":
                return {"status": "completed", "chunk_count": existing.completed}
            if existing is not None and existing.space_id != provider.space.id:
                # A stale/misconfigured worker cannot regress a completed job or
                # overwrite a live owner's status. Apply the same lease fence in SQL.
                await session.execute(update(EmbeddingRebuild).where(
                    EmbeddingRebuild.id == rebuild_id,
                    EmbeddingRebuild.status != "completed",
                    or_(EmbeddingRebuild.lease_until.is_(None), EmbeddingRebuild.lease_until < now),
                ).values(status="failed", error="Worker configuration does not match this rebuild. Align API and worker settings, then retry."))
                await session.commit()
                raise ValueError("Worker configuration does not match the requested embedding space.")
            return {"status": "busy", "retry_after": max(1, int(
                ((existing.lease_until or now) - now).total_seconds()) + 1)}
        project_id = job.project_id

    async def owned_job(session):
        job = await session.scalar(select(EmbeddingRebuild).where(
            EmbeddingRebuild.id == rebuild_id, EmbeddingRebuild.claim_token == token).with_for_update())
        if job is None:
            raise RuntimeError("Embedding rebuild claim was superseded.")
        job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        return job

    try:
        while True:
            async with session_factory() as session:
                job = await owned_job(session)
                # Content hashes also defend against in-place corrections to an existing chunk.
                chunks = list((await session.execute(select(KnowledgeChunk).where(
                    KnowledgeChunk.project_id == project_id).order_by(KnowledgeChunk.id))).scalars())
                items = {item.chunk_id: item for item in (await session.execute(
                    select(EmbeddingRebuildItem).where(EmbeddingRebuildItem.rebuild_id == rebuild_id)
                )).scalars()}
                pending = [chunk for chunk in chunks if chunk.id not in items
                           or items[chunk.id].vector is None
                           or items[chunk.id].content_hash != content_hash(chunk.content)]
                job.total = len(chunks)
                job.completed = len(chunks) - len(pending)
                job.failed = sum(1 for item in items.values() if item.error)
                batch = [(c.id, c.content, content_hash(c.content)) for c in pending[:batch_size]]
                await session.commit()
            if not batch:
                break
            # No open session/locks across an external call.
            try:
                vectors = await provider.embed([text for _, text, _ in batch])
                validate_vectors(vectors, len(batch), provider.dimensions)
            except Exception as exc:
                async with session_factory() as session:
                    job = await owned_job(session)
                    for chunk_id, _, digest in batch:
                        # Reprocessing can delete source chunks while the provider runs.
                        if await session.get(KnowledgeChunk, chunk_id) is None:
                            continue
                        await session.execute(insert(EmbeddingRebuildItem).values(
                            id=uuid.uuid4(), rebuild_id=rebuild_id, chunk_id=chunk_id,
                            content_hash=digest, attempts=1, error=type(exc).__name__,
                        ).on_conflict_do_update(index_elements=["rebuild_id", "chunk_id"],
                            set_={"attempts": EmbeddingRebuildItem.attempts + 1,
                                  "error": type(exc).__name__, "vector": None}))
                    job.status = "failed"
                    job.error = f"Embedding batch failed: {type(exc).__name__}. Retry resumes unfinished chunks."
                    job.failed = len(batch)
                    job.claim_token = None
                    job.lease_until = None
                    await session.commit()
                raise
            async with session_factory() as session:
                await owned_job(session)
                await lock_project(session, project_id)
                for (chunk_id, _, digest), vector in zip(batch, vectors, strict=True):
                    chunk = await session.get(KnowledgeChunk, chunk_id)
                    if chunk is None or content_hash(chunk.content) != digest:
                        continue
                    await session.execute(insert(EmbeddingRebuildItem).values(
                        id=uuid.uuid4(), rebuild_id=rebuild_id, chunk_id=chunk_id,
                        content_hash=digest, vector=vector, attempts=1,
                    ).on_conflict_do_update(index_elements=["rebuild_id", "chunk_id"], set_={
                        "content_hash": digest, "vector": vector,
                        "attempts": EmbeddingRebuildItem.attempts + 1, "error": None}))
                await session.commit()

        async with session_factory() as session:
            job = await owned_job(session)
            project = await lock_project(session, project_id)
            chunks = list((await session.execute(select(KnowledgeChunk).where(
                KnowledgeChunk.project_id == project_id))).scalars())
            items = {i.chunk_id: i for i in (await session.execute(select(EmbeddingRebuildItem).where(
                EmbeddingRebuildItem.rebuild_id == rebuild_id))).scalars()}
            # Validate the complete current corpus again inside the activation transaction.
            for chunk in chunks:
                item = items.get(chunk.id)
                if item is None or item.vector is None or item.content_hash != content_hash(chunk.content):
                    raise ValueError("Corpus changed before activation. Resume the rebuild.")
                validate_vectors([item.vector], 1, provider.dimensions)
            if not chunks:
                raise ValueError("Cannot activate an empty project.")
            for chunk in chunks:
                chunk.embedding = items[chunk.id].vector
                chunk.embedding_space_id = provider.space.id
                chunk.embedding_space = provider.space.as_dict()
                chunk.embedding_provider = provider.name
                chunk.embedding_status = EmbeddingStatus.COMPLETED
            project.active_embedding_space_id = provider.space.id
            job.status, job.completed, job.total, job.failed = "completed", len(chunks), len(chunks), 0
            job.error, job.claim_token, job.lease_until = None, None, None
            await session.commit()
        return {"status": "completed", "chunk_count": len(chunks)}
    except Exception:
        async with session_factory() as session:
            await session.execute(update(EmbeddingRebuild).where(
                EmbeddingRebuild.id == rebuild_id, EmbeddingRebuild.claim_token == token,
            ).values(status="failed", error="Rebuild interrupted or validation failed; resume to retry.",
                     claim_token=None, lease_until=None))
            await session.commit()
        raise
