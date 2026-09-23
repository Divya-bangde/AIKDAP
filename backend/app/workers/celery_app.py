"""Celery application instance and configuration.

Reads the broker and result-backend URLs from the application
`Settings` object — no hardcoded connection strings — so the worker
process uses exactly the same Redis configuration as the rest of the
app. This module only builds and configures the `Celery` instance;
`worker.py` is the process entry point (logging setup, explicit task
registration, the `-A` target Docker/CLI actually invoke).
"""

import asyncio
import sys

from celery import Celery

from app.core.config import settings

# The Celery worker process has its own independent import graph,
# separate from the FastAPI app and from Alembic's env.py. `Asset` has
# string-based `ForeignKey("users.id")` / `ForeignKey("projects.id")`
# references that SQLAlchemy only resolves once those tables are
# registered on `Base.metadata` — which requires their model modules
# to have been imported *somewhere* in this process. Without this,
# configuring the `Asset` mapper fails with `NoReferencedTableError`
# the first time a task actually touches the database.
from app.modules.assets.models import Asset  # noqa: F401
from app.modules.auth.models import User  # noqa: F401
from app.modules.knowledge_base.models import ChunkPosition, KnowledgeChunk  # noqa: F401
from app.modules.papers.models import PaperReference  # noqa: F401
from app.modules.projects.models import Project  # noqa: F401
from app.modules.tasks.models import Task  # noqa: F401

if sys.platform == "win32":
    # Mirrors the guard in app/database/session.py and alembic/env.py:
    # psycopg's async driver requires the selector event loop. This
    # runs once at worker-process import time, before any task calls
    # asyncio.run(), so the policy is already correct by then.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

celery_app = Celery("aikdap", broker=settings.celery_broker_url, backend=settings.celery_result_backend)

# Auto-discovery, not a hardcoded `include=[...]` list: for every
# package in the first argument, Celery imports `<package>.<related_name>`
# if it exists. Called twice so both `app/workers/tasks.py` and
# `app/workers/scheduler.py` are picked up without this file needing to
# know each individual task module by name — adding a new task module
# under `app.workers` with one of these two names is enough.
celery_app.autodiscover_tasks(["app.workers"], related_name="tasks")
celery_app.autodiscover_tasks(["app.workers"], related_name="scheduler")

celery_app.conf.update(
    # Serialization: JSON only. Never enable the pickle serializer —
    # deserializing pickled messages from the broker is arbitrary code
    # execution if the broker is ever compromised or misconfigured.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone: store all timestamps in UTC, matching every other
    # `DateTime(timezone=True)` column in the app.
    timezone="UTC",
    enable_utc=True,
    # Reliability: a task is only acknowledged (removed from the
    # broker) after it finishes, not merely after the worker picks it
    # up. Combined with `task_reject_on_worker_lost`, a worker that
    # crashes or is killed mid-task doesn't silently lose that task —
    # it's redelivered to another worker. This trades "at-most-once"
    # for "at-least-once" delivery, which is the correct default for
    # a pipeline that writes durable DB state (our tasks are already
    # idempotent — see `AssetProcessingService.process_asset` and
    # `KnowledgeBaseService.replace_chunks_for_asset`).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One task at a time per worker process: prevents a single worker
    # from prefetching a large batch of slow tasks while other workers
    # sit idle. Matters once processing tasks do real (slow) I/O —
    # OCR/embedding calls in future sprints.
    worker_prefetch_multiplier=1,
    # Retry connecting to the broker on worker startup instead of
    # crashing immediately — relevant in Docker Compose, where the
    # worker container can start slightly before Redis finishes its
    # healthcheck despite `depends_on: condition: service_healthy`.
    broker_connection_retry_on_startup=True,
    # Visibility into what's running, without keeping results forever.
    task_track_started=True,
    result_expires=3600,
    # Let the app's own structured logging (see `worker.py`) own log
    # formatting instead of Celery's default handler taking over the
    # root logger.
    worker_hijack_root_logger=False,
)
