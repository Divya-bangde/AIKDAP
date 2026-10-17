"""Real pgvector persistence/recovery; deterministic providers, no model service."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import httpx
from sqlalchemy import select

from app.database.session import async_session_factory
from app.modules.assets.enums import EmbeddingStatus
from app.modules.knowledge_base.embeddings import EmbeddingProvider
from app.modules.knowledge_base.enums import EmbeddingProviderName
from app.modules.knowledge_base.models import EmbeddingRebuildItem, KnowledgeChunk
from app.modules.knowledge_base.rebuild import readiness, request_rebuild, run_rebuild
from app.modules.knowledge_base.repository import KnowledgeChunkRepository
from app.modules.knowledge_base.spaces import EmbeddingSpace
from app.main import app
from app.modules.auth.security import create_access_token


class ScriptedProvider(EmbeddingProvider):
    def __init__(self, model="bge", *, fail_on=None, invalid=False):
        self.model, self.fail_on, self.invalid = model, fail_on, invalid
        self.calls = []

    @property
    def name(self):
        return EmbeddingProviderName.LOCAL if self.model == "bge" else EmbeddingProviderName.JINA_AI

    @property
    def dimensions(self):
        return 1024

    @property
    def space(self):
        return EmbeddingSpace(self.name.value, self.model, "revision-1", 1024, "passage", "query")

    async def embed(self, texts):
        self.calls.extend(texts)
        if self.fail_on in texts:
            raise RuntimeError("Injected provider interruption")
        return [[0.0] * 1024 if self.invalid else [1.0] + [0.01] * 1023 for _ in texts]


async def make_chunks(session, project, make_report_asset, *, count=3, provider=None):
    asset = await make_report_asset(project)
    chunks = [KnowledgeChunk(
        id=uuid.UUID(int=index + 1), project_id=project.id, asset_id=asset.id,
        chunk_index=index, content=f"passage {index}",
        embedding=[1.0] + [0.01] * 1023,
        embedding_status=EmbeddingStatus.COMPLETED,
        embedding_space_id=provider.space.id if provider else None,
        embedding_space=provider.space.as_dict() if provider else None,
    ) for index in range(count)]
    session.add_all(chunks)
    if provider:
        project.active_embedding_space_id = provider.space.id
    await session.commit()
    return chunks


@pytest.mark.asyncio
async def test_legacy_and_other_1024_spaces_cannot_be_searched(session, project, make_report_asset):
    chunks = await make_chunks(session, project, make_report_asset)
    repo = KnowledgeChunkRepository(session)
    bge, jina = ScriptedProvider(), ScriptedProvider("jina")
    query = dict(query_vector=[1.0] + [0.01] * 1023, project_id=project.id)
    assert await repo.search_similar(project.owner_id, space_id=bge.space.id, **query) == []
    assert (await readiness(session, project.id, bge))["legacy_chunks"] == 3
    for chunk in chunks:
        chunk.embedding_space_id = bge.space.id
        chunk.embedding_space = bge.space.as_dict()
    project.active_embedding_space_id = bge.space.id
    await session.commit()
    assert len(await repo.search_similar(project.owner_id, space_id=bge.space.id, **query)) == 3
    assert await repo.search_similar(project.owner_id, space_id=jina.space.id, **query) == []
    assert await repo.search_similar(uuid.uuid4(), space_id=bge.space.id, **query) == []
    assert (await readiness(session, project.id, jina))["searchable_chunks"] == 0


@pytest.mark.asyncio
async def test_interruption_resumes_successful_batches_and_activates_atomically(session, project, make_report_asset):
    old, target = ScriptedProvider(), ScriptedProvider("jina", fail_on="passage 1")
    await make_chunks(session, project, make_report_asset, provider=old)
    job = await request_rebuild(session, project.id, target)
    with pytest.raises(RuntimeError, match="Injected"):
        await run_rebuild(async_session_factory, job.id, provider=target, batch_size=1)
    await session.refresh(project)
    await session.refresh(job)
    assert project.active_embedding_space_id == old.space.id
    assert job.status == "failed"
    items = list((await session.execute(select(EmbeddingRebuildItem).where(
        EmbeddingRebuildItem.rebuild_id == job.id))).scalars())
    assert sum(item.vector is not None for item in items) == 1
    target.fail_on = None
    target.calls.clear()
    assert (await run_rebuild(async_session_factory, job.id, provider=target, batch_size=1))["status"] == "completed"
    assert target.calls == ["passage 1", "passage 2"]
    await session.refresh(project)
    await session.refresh(job)
    result = await readiness(session, project.id, target)
    assert result["ready"] and result["searchable_chunks"] == result["total_chunks"] == 3
    assert project.active_embedding_space_id == target.space.id
    assert job.failed == 0 and job.completed == 3
    target.calls.clear()
    await run_rebuild(async_session_factory, job.id, provider=target)
    assert target.calls == []  # Redelivery of a completed job does no work.


@pytest.mark.asyncio
async def test_invalid_space_never_activates_and_expired_claim_recovers(session, project, make_report_asset):
    old, target = ScriptedProvider(), ScriptedProvider("jina", invalid=True)
    await make_chunks(session, project, make_report_asset, provider=old)
    job = await request_rebuild(session, project.id, target)
    job.status = "running"
    job.claim_token = uuid.uuid4()
    job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=60)
    await session.commit()
    assert (await run_rebuild(async_session_factory, job.id, provider=target))["status"] == "busy"
    assert target.calls == []
    job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(ValueError, match="Zero"):
        await run_rebuild(async_session_factory, job.id, provider=target)
    await session.refresh(project)
    assert project.active_embedding_space_id == old.space.id
    target.invalid = False
    await run_rebuild(async_session_factory, job.id, provider=target)
    await session.refresh(project)
    assert project.active_embedding_space_id == target.space.id


@pytest.mark.asyncio
async def test_concurrent_workers_only_one_calls_provider(session, project, make_report_asset):
    entered, release = asyncio.Event(), asyncio.Event()
    provider = ScriptedProvider()
    original_embed = provider.embed

    async def delayed(texts):
        entered.set()
        await release.wait()
        return await original_embed(texts)

    provider.embed = delayed
    await make_chunks(session, project, make_report_asset)
    job = await request_rebuild(session, project.id, provider)
    first = asyncio.create_task(run_rebuild(async_session_factory, job.id, provider=provider))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        duplicate = await run_rebuild(async_session_factory, job.id, provider=provider)
        assert duplicate["status"] == "busy"
    finally:
        release.set()
        await first
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_rebuild_api_dispatch_failure_is_retryable_without_losing_checkpoint(session, project, make_report_asset, monkeypatch):
    from unittest.mock import Mock
    from app.workers.scheduler import rebuild_embeddings

    provider = ScriptedProvider()
    await make_chunks(session, project, make_report_asset)
    monkeypatch.setattr("app.modules.knowledge_base.router.get_embedding_provider", lambda: provider)
    dispatch = Mock(side_effect=RuntimeError("Broker unavailable"))
    monkeypatch.setattr(rebuild_embeddings, "apply_async", dispatch)
    headers = {"Authorization": f"Bearer {create_access_token(project.owner_id)}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/knowledge-base/rebuild", params={"project_id": str(project.id)}, headers=headers)
        assert first.status_code == 202, first.text
        assert first.json()["rebuild"]["status"] == "failed"
        assert "dispatch failed" in first.json()["rebuild"]["error"]
        dispatch.side_effect = None
        second = await client.post("/api/v1/knowledge-base/rebuild", params={"project_id": str(project.id)}, headers=headers)
        assert second.status_code == 202 and second.json()["rebuild"]["status"] == "queued"
        assert first.json()["rebuild"]["id"] == second.json()["rebuild"]["id"]


@pytest.mark.asyncio
async def test_mismatched_worker_preserves_live_claim_and_completed_status(session, project, make_report_asset):
    correct, wrong = ScriptedProvider(), ScriptedProvider("jina")
    await make_chunks(session, project, make_report_asset)
    job = await request_rebuild(session, project.id, correct)
    token = uuid.uuid4()
    job.status, job.claim_token = "running", token
    job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=60)
    await session.commit()
    with pytest.raises(ValueError, match="Worker configuration"):
        await run_rebuild(async_session_factory, job.id, provider=wrong)
    await session.refresh(job)
    assert job.status == "running" and job.claim_token == token and job.error is None
    job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    await run_rebuild(async_session_factory, job.id, provider=correct)
    assert (await run_rebuild(async_session_factory, job.id, provider=wrong))["status"] == "completed"
    await session.refresh(job)
    assert job.status == "completed" and job.error is None
