"""Celery worker process entry point.

This is the `-A` (app) target Docker and the CLI use:

    celery -A app.workers.worker worker --loglevel=info

Responsibilities that belong here rather than in `celery_app.py`:
configuring the app's own structured logging for this process, and
explicitly importing the task modules so `celery -A app.workers.worker
inspect registered` reflects them immediately — `celery_app.py`'s
`autodiscover_tasks` calls already do this lazily on worker startup,
so the imports below are redundant-but-explicit rather than load-bearing,
matching the "explicit is better than implicit" register-tasks
responsibility called out for this module.

`app` is deliberately the name of the re-exported Celery instance:
Celery's `-A` flag looks for an attribute named `app` (or `celery`) in
the target module by convention, and `app = celery_app` is what makes
`-A app.workers.worker` resolve correctly.
"""

import asyncio

from celery.signals import worker_ready

from app.core.logging.logger import configure_logging, get_logger
from app.database.session import configure_for_worker_process
from app.workers.celery_app import celery_app

configure_logging()
logger = get_logger(__name__)

# Sprint 16 Phase 8.0 -- MUST run before any task/reconciliation module
# is imported below: those modules bind their own `from app.database.
# session import async_session_factory` NAME at import time, so
# reconfiguring the module's globals AFTER they've already imported the
# old ones would not retroactively fix anything. See `configure_for_
# worker_process`'s own docstring for the real, empirically-confirmed
# cross-event-loop `asyncpg` pooling bug this closes.
configure_for_worker_process()

# Explicit registration (see module docstring) — both modules register
# their tasks on `celery_app` purely by being imported.
import app.workers.tasks  # noqa: E402,F401
import app.workers.scheduler  # noqa: E402,F401
from app.workers.reconciliation import (  # noqa: E402
    reconcile_execution_jobs_on_startup,
    reconcile_stale_launching_execution_jobs,
    reconcile_stale_report_generations_on_startup,
    reconcile_stale_research_runs_on_startup,
)
# reconcile_stale_docker_managed_attempts (Sprint 16 Phase 7B.29) is
# deliberately NOT imported/called separately here -- it already runs as
# the third step inside `reconcile_execution_jobs_on_startup` itself,
# matching how `reconcile_stale_validating_execution_jobs`/`reconcile_
# stale_pending_execution_attempts` are wired.

app = celery_app


async def _run_startup_reconciliation() -> None:
    """Runs every startup reconciler in ONE event loop.

    Deliberately a single `asyncio.run()` around all three calls below,
    not separate ones. This function's own `asyncio.run()` call is the
    FIRST one this worker process ever makes, and the very NEXT one is
    `app.workers.tasks._run_task_loop`'s, for whatever real task Celery
    dispatches first -- both this call and every task afterward run
    against the `NullPool`-backed engine `configure_for_worker_process()`
    (called at worker import time, see this module's own docstring)
    already established, so there is no pooled `asyncpg` connection
    left behind for a later `asyncio.run()` to incorrectly reuse across
    a now-closed event loop. See `app.database.session.configure_for_
    worker_process`'s own docstring for the full, empirically-confirmed
    story of the bug this closes and why a simpler per-call `engine.
    dispose()` (tried first) was not sufficient on the real worker.

    `reconcile_stale_launching_execution_jobs` (Phase 7B.22) is safe to
    call from this "fast, never-raises" path even though it drives real
    retry recovery, because it only detects and enqueues here -- the
    guard pipeline and the claim transaction run later, inside the
    Celery task it enqueues, off this loop entirely.
    """
    await reconcile_stale_research_runs_on_startup()
    await reconcile_stale_report_generations_on_startup()
    await reconcile_execution_jobs_on_startup()
    await reconcile_stale_launching_execution_jobs()


@worker_ready.connect
def _reconcile_on_startup(**_kwargs: object) -> None:
    """Sprint 9J (research runs) + Sprint 16 Phase 7B.17 (execution jobs
    and attempts): recover rows orphaned by a previous instance of this
    worker that never returned (crash, OOM, container restart). Runs
    once, after Celery reports this process ready to accept tasks — see
    `app.workers.reconciliation` for why this replaces a Celery Beat
    schedule this project does not have.

    Nothing here needs its own `try`/`except`: both entry points awaited
    by `_run_startup_reconciliation` already have their own "never
    raises" contract (`reconcile_stale_research_runs_on_startup` for one
    row type, `reconcile_execution_jobs_on_startup` for the other two) --
    a failure inside either is already handled and logged before control
    returns here.
    """
    asyncio.run(_run_startup_reconciliation())


logger.info(
    "worker_process_initialized",
    registered_tasks=sorted(
        name for name in celery_app.tasks if not name.startswith("celery.")
    ),
)
