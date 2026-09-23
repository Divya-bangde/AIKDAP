"""Async SQLAlchemy engine, session factory, and FastAPI DB dependency.

A single module-level async engine and session factory are shared across
the application; `get_db` is the dependency-injection entrypoint routers
and services use to obtain a request-scoped `AsyncSession`.
"""

import asyncio
import sys
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

if sys.platform == "win32":
    # psycopg's async driver requires the selector event loop; asyncio's
    # default ProactorEventLoop on Windows cannot drive it.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _build_engine(*, null_pool: bool = False) -> AsyncEngine:
    """Constructs the async engine -- factored out (Sprint 16 Phase 8.0)
    so `configure_for_worker_process()` below can rebuild it with a
    different pool strategy without duplicating the connection-string/
    echo wiring."""
    if null_pool:
        return create_async_engine(
            settings.database_url, echo=settings.db_echo, poolclass=NullPool
        )
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


def _build_session_factory(bound_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=bound_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


engine: AsyncEngine = _build_engine()
async_session_factory: async_sessionmaker[AsyncSession] = _build_session_factory(engine)


def open_session() -> AsyncSession:
    """A new session from the *current* factory.

    For writes that must not share the caller's transaction (workflow
    step events, chunk positions). Reads the module global at call time,
    so it follows `configure_for_worker_process` inside Celery workers.
    """
    return async_session_factory()


def configure_for_worker_process() -> None:
    """Rebuilds the module-level `engine`/`async_session_factory` with
    `NullPool` (Sprint 16 Phase 8.0) -- MUST be called from `app.workers.
    worker` before `app.workers.tasks` (or anything else that binds its
    own `from app.database.session import async_session_factory` name)
    is ever imported, since reassigning the globals here does not
    retroactively update a name another module already imported by
    value.

    Why this exists, confirmed live (not theorized) against a real
    Celery worker consuming from a real Redis broker: `app.workers.
    tasks._run_task_loop` drives every task through its OWN `asyncio.
    run()` call, and a long-lived worker process makes MANY such calls
    over its lifetime (one per task, plus one for `worker_ready`'s
    startup reconciliation) -- all sharing this ONE process-lifetime
    connection pool. `asyncpg` connections are bound to the event loop
    that created them; `asyncio.run()` closes its loop on return. An
    `await engine.dispose()` added after every `_run_task_loop` call
    (and after startup reconciliation) was tried FIRST and verified
    correct in isolation (a standalone repro script disposing between
    three sequential `asyncio.run()` calls never failed) -- but the
    real worker process, under real Celery/kombu machinery, still hit
    `RuntimeError: ... attached to a different loop` on a later task's
    very first database call, even with disposal in place. The
    isolated repro could not reproduce whatever real-worker-specific
    interaction causes a connection to survive disposal, so rather than
    continue chasing that root cause, this closes the entire class of
    bug structurally: `NullPool` never holds a connection between
    checkouts, so there is never a stale one to reuse, regardless of
    the exact mechanism. Confirmed live afterward: a Celery worker
    processing a sequence of real `launch_execution_job`/`reconcile_
    execution_attempt` tasks no longer fails on the second (or later)
    task.

    Deliberately NOT applied to the module-level default engine every
    process gets: the FastAPI/uvicorn API process runs ONE persistent
    event loop for its entire lifetime (never calls `asyncio.run()` per
    request), so pooled connection reuse there is a genuine benefit
    with no cross-loop risk -- this function only ever runs in the
    worker process.
    """
    global engine, async_session_factory
    engine = _build_engine(null_pool=True)
    async_session_factory = _build_session_factory(engine)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped `AsyncSession` for use with `Depends`.

    Rolls back the transaction if the caller raises, and always closes
    the session, returning its connection to the pool.
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
