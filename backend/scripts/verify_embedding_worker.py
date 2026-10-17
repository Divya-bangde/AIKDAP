"""Real disposable Celery/Redis/pgvector rebuild diagnostic with a fake model.

From backend: .venv/Scripts/python.exe scripts/verify_embedding_worker.py
Optional --worker-log PATH preserves the actual solo worker's output.
Uses a deterministic provider, not a live model. Verifies real broker delivery,
Celery retry, durable batch resumption, atomic activation and completed-job
redelivery. Does not simulate a hard worker kill or verify live provider quality.
"""

import argparse
import asyncio
import gc
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import psycopg
import redis
from check import ROOT, run
from pydantic_settings import DotEnvSettingsSource, EnvSettingsSource

sys.path.insert(0, str(ROOT))


def service_values():
    database = os.environ["TEST_DATABASE_URL"]
    broker = os.environ["TEST_REDIS_URL"]
    for value in (database, broker):
        assert urlsplit(value).hostname == "127.0.0.1", (
            "Disposable loopback services required"
        )
    assert urlsplit(database).path.startswith("/aikdap_test"), "Disposable DB required"
    return dict(
        database_url=database,
        celery_broker_url=broker,
        celery_result_backend=broker,
        secret_key="isolated-worker-diagnostic-never-use-in-production",
    )


def load_application():
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    with (
        patch.object(EnvSettingsSource, "__call__", return_value=service_values()),
        patch.object(DotEnvSettingsSource, "__call__", return_value={}),
    ):
        from app.core.config import settings
    from app.workers.worker import app

    return app, settings


def fixture_provider():
    from celery import current_task

    from app.modules.knowledge_base.embeddings import EmbeddingProvider
    from app.modules.knowledge_base.enums import EmbeddingProviderName
    from app.modules.knowledge_base.spaces import EmbeddingSpace

    class DeterministicFixture(EmbeddingProvider):
        @property
        def name(self):
            return EmbeddingProviderName.LOCAL

        @property
        def dimensions(self):
            return 1024

        @property
        def space(self):
            return EmbeddingSpace("diagnostic-fixture", "new-model", "1", 1024)

        async def embed(self, texts):
            registry = redis.Redis.from_url(
                os.environ["TEST_REDIS_URL"],
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3,
            )
            retries = current_task.request.retries
            registry.rpush(
                "fixture:calls", json.dumps(dict(texts=texts, retries=retries))
            )
            if texts[0] == "passage 32" and registry.set(
                "fixture:injected", "1", nx=True
            ):
                database = os.environ["TEST_DATABASE_URL"].replace("+psycopg", "")
                with psycopg.connect(database) as connection:
                    staged = connection.execute(
                        "SELECT count(*) FROM embedding_rebuild_items "
                        "WHERE vector IS NOT NULL"
                    ).fetchone()[0]
                    active = connection.execute(
                        "SELECT active_embedding_space_id FROM projects"
                    ).fetchone()[0]
                    activated = connection.execute(
                        "SELECT count(*) FROM knowledge_chunks "
                        "WHERE embedding_space_id=%s",
                        (self.space.id,),
                    ).fetchone()[0]
                assert staged == 32 and active != self.space.id and activated == 0
                registry.set(
                    "fixture:pre_failure",
                    json.dumps(
                        dict(staged=staged, active=active, target_chunks=activated)
                    ),
                )
                raise RuntimeError(
                    "Injected once: second deterministic embedding batch"
                )
            return [[1.0] + [0.02] * 1023 for _ in texts]

    return DeterministicFixture()


def worker_main():
    app, _ = load_application()
    from celery.signals import task_retry, worker_ready

    from app.modules.knowledge_base import rebuild
    from app.workers.scheduler import rebuild_embeddings

    rebuild.get_embedding_provider = lambda: fixture_provider()
    # Test-only scheduling acceleration; production retry policy remains untouched.
    rebuild_embeddings.default_retry_delay = 1
    registry = redis.Redis.from_url(
        os.environ["TEST_REDIS_URL"],
        decode_responses=True,
        socket_timeout=3,
        socket_connect_timeout=3,
    )

    @worker_ready.connect(weak=False)
    def mark_ready(**_kwargs):
        registry.set("fixture:worker_ready", "1")

    @task_retry.connect(weak=False)
    def mark_retry(request, reason, **_kwargs):
        registry.rpush(
            "fixture:retries",
            json.dumps(
                dict(
                    task=request.task,
                    retries=request.retries,
                    reason=type(reason).__name__,
                )
            ),
        )

    app.worker_main(
        [
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=INFO",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "--queues=" + os.environ["DIAGNOSTIC_QUEUE"],
        ]
    )


async def seed():
    from app.database.session import async_session_factory
    from app.modules.assets.enums import (
        AssetProcessingStatus,
        AssetSource,
        AssetStatus,
        AssetType,
        EmbeddingStatus,
    )
    from app.modules.assets.models import Asset
    from app.modules.auth.models import User
    from app.modules.knowledge_base.models import KnowledgeChunk
    from app.modules.knowledge_base.rebuild import readiness, request_rebuild
    from app.modules.knowledge_base.spaces import EmbeddingSpace
    from app.modules.projects.models import Project, ProjectStatus, ProjectType

    provider = fixture_provider()
    old = EmbeddingSpace("diagnostic-fixture", "old-model", "1", 1024)
    async with async_session_factory() as session:
        user = User(
            email="embedding-worker-fixture@example.com",
            hashed_password="not-a-real-hash",
            is_active=True,
        )
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            name="65 chunk worker diagnostic",
            project_type=ProjectType.RESEARCH,
            status=ProjectStatus.ACTIVE,
            active_embedding_space_id=old.id,
        )
        session.add(project)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            owner_id=user.id,
            title="Fixture paper",
            asset_type=AssetType.DOCUMENT,
            status=AssetStatus.ACTIVE,
            source=AssetSource.UPLOAD,
            mime_type="text/plain",
            file_name="fixture.txt",
            file_extension="txt",
            file_size=650,
            storage_path="unused-fixture",
            checksum="a" * 64,
            version=1,
            tags=[],
            asset_metadata={},
            ai_profile={},
            processing_status=AssetProcessingStatus.COMPLETED,
        )
        session.add(asset)
        await session.flush()
        session.add_all(
            [
                KnowledgeChunk(
                    id=uuid.UUID(int=index + 1),
                    project_id=project.id,
                    asset_id=asset.id,
                    chunk_index=index,
                    content=f"passage {index}",
                    embedding=[1.0] + [0.01] * 1023,
                    embedding_status=EmbeddingStatus.COMPLETED,
                    embedding_space_id=old.id,
                    embedding_space=old.as_dict(),
                )
                for index in range(65)
            ]
        )
        await session.commit()
        initial = await readiness(session, project.id, provider)
        assert not initial["ready"] and initial["searchable_chunks"] == 0
        job = await request_rebuild(session, project.id, provider)
        return project.id, job.id, old.id


async def verify_database(project_id, job_id):
    from sqlalchemy import select

    from app.database.session import async_session_factory
    from app.modules.knowledge_base.models import EmbeddingRebuildItem
    from app.modules.knowledge_base.rebuild import readiness

    async with async_session_factory() as session:
        status = await readiness(session, project_id, fixture_provider())
        assert status["ready"] and status["compatible"]
        assert status["rebuild"]["status"] == "completed"
        assert status["total_chunks"] == status["searchable_chunks"] == 65
        assert status["rebuild"]["completed"] == 65 and status["rebuild"]["failed"] == 0
        items = list(
            (
                await session.scalars(
                    select(EmbeddingRebuildItem)
                    .where(EmbeddingRebuildItem.rebuild_id == job_id)
                    .order_by(EmbeddingRebuildItem.chunk_id)
                )
            ).all()
        )
        assert len(items) == 65 and all(item.vector is not None for item in items)
        assert [item.attempts for item in items] == [1] * 32 + [2] * 32 + [1]
        return status


def wait_until(predicate, timeout, worker):
    deadline = time.monotonic() + timeout
    while not predicate():
        if worker.poll() is not None:
            raise RuntimeError("Diagnostic worker exited before completion")
        if time.monotonic() >= deadline:
            raise TimeoutError("Diagnostic worker exceeded bounded wait")
        time.sleep(0.1)


def main(worker_log):
    containers, worker = [], None
    prefix = "aikdap-test-embedding-" + uuid.uuid4().hex[:10]
    try:
        for suffix, image, port, options in (
            (
                "pg",
                "pgvector/pgvector:pg17",
                "5432",
                [
                    "-e",
                    "POSTGRES_USER=test",
                    "-e",
                    "POSTGRES_PASSWORD=test",
                    "-e",
                    "POSTGRES_DB=aikdap_test_worker",
                ],
            ),
            ("redis", "redis:8-alpine", "6379", []),
        ):
            name = f"{prefix}-{suffix}"
            run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    name,
                    "-p",
                    f"127.0.0.1::{port}",
                    *options,
                    image,
                ],
                timeout=120,
            )
            containers.append(name)
            mapped = run(
                ["docker", "port", name, port], capture=True, timeout=10
            ).rsplit(":", 1)[1]
            if suffix == "pg":
                os.environ["TEST_DATABASE_URL"] = (
                    f"postgresql+psycopg://test:test@127.0.0.1:{mapped}/aikdap_test_worker?connect_timeout=5"
                )
            else:
                os.environ["TEST_REDIS_URL"] = f"redis://127.0.0.1:{mapped}/0"
        deadline = time.monotonic() + 30
        while True:
            try:
                run(
                    ["docker", "exec", containers[0], "pg_isready", "-U", "test"],
                    capture=True,
                    timeout=5,
                )
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Disposable PostgreSQL unavailable")
                time.sleep(0.5)
        env = os.environ.copy()
        env.update({key.upper(): value for key, value in service_values().items()})
        env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        env["DIAGNOSTIC_QUEUE"] = prefix
        # Celery itself reads CELERY_* environment variables ahead of app.conf.
        # Set the parent's explicit disposable values as well as the child's.
        os.environ.update(
            {key.upper(): value for key, value in service_values().items()}
        )
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, timeout=60)
        app, _ = load_application()
        app.conf.update(
            broker_connection_timeout=3,
            redis_socket_connect_timeout=3,
            redis_socket_timeout=3,
            result_backend_transport_options={
                "retry_policy": {"max_retries": 1, "timeout": 3}
            },
        )
        project_id, job_id, old_id = asyncio.run(seed())
        registry = redis.Redis.from_url(
            os.environ["TEST_REDIS_URL"],
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
        )
        worker_log.parent.mkdir(parents=True, exist_ok=True)
        with worker_log.open("w", encoding="utf-8") as output:
            worker = subprocess.Popen(
                [sys.executable, __file__, "--worker"],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            wait_until(lambda: registry.get("fixture:worker_ready"), 60, worker)
            result = app.send_task(
                "workers.rebuild_embeddings",
                args=[str(job_id)],
                queue=prefix,
                retry=False,
            )
            wait_until(result.ready, 90, worker)
            assert result.get(timeout=3) == {"status": "completed", "chunk_count": 65}
            calls = [
                json.loads(value) for value in registry.lrange("fixture:calls", 0, -1)
            ]
            assert [len(call["texts"]) for call in calls] == [32, 32, 32, 1]
            assert [call["retries"] for call in calls] == [0, 0, 1, 1]
            assert calls[0]["texts"] == [f"passage {i}" for i in range(32)]
            assert calls[1]["texts"] == calls[2]["texts"]
            assert calls[3]["texts"] == ["passage 64"]
            retries = [
                json.loads(value) for value in registry.lrange("fixture:retries", 0, -1)
            ]
            assert (
                len(retries) == 1 and retries[0]["task"] == "workers.rebuild_embeddings"
            )
            before = json.loads(registry.get("fixture:pre_failure"))
            assert before == dict(staged=32, active=old_id, target_chunks=0)
            final = asyncio.run(verify_database(project_id, job_id))
            duplicate = app.send_task(
                "workers.rebuild_embeddings",
                args=[str(job_id)],
                queue=prefix,
                retry=False,
            )
            wait_until(duplicate.ready, 30, worker)
            assert duplicate.get(timeout=3) == {
                "status": "completed",
                "chunk_count": 65,
            }
            assert registry.llen("fixture:calls") == 4
            print(
                "PASS real Redis/Celery solo worker: one retry, "
                "batch sizes [32,32,32,1], "
                "first successful 32 never repeated",
                flush=True,
            )
            print(
                "PASS atomic activation: old space retained with 32 staged vectors; "
                "final readiness/searchable coverage 65/65; "
                "item attempts 32x1,32x2,1x1",
                flush=True,
            )
            print(
                "PASS broker redelivery of completed rebuild "
                "performs no embedding calls",
                flush=True,
            )
            print(
                "Provider: deterministic fixture (not live); "
                "hard worker death not exercised",
                flush=True,
            )
            print("Final readiness:", json.dumps(final, sort_keys=True), flush=True)
            # Release subscriptions while disposable Redis still exists. Celery's
            # AsyncResult destructor otherwise attempts unsubscribe after cleanup.
            result.forget()
            duplicate.forget()
            del result, duplicate
            gc.collect()
            app.backend.result_consumer.stop()
            app.close()
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=10)
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-f", name], timeout=20, check=False)
        print("Worker log:", worker_log, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-log", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker_main()
    else:
        path = args.worker_log or Path(tempfile.gettempdir()) / (
            "aikdap-embedding-worker-" + uuid.uuid4().hex[:10] + ".log"
        )
        main(path.resolve())
