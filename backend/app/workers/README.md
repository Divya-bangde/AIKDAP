# Worker Infrastructure

Background task processing for AIKDAP, built on Celery with Redis as
both broker and result backend. This is where the asset processing
pipeline (extract → chunk → future embed) actually runs, outside the
FastAPI request/response cycle.

## Files

| File | Responsibility |
|---|---|
| `celery_app.py` | Builds and configures the single `Celery` instance: broker/backend URLs from `Settings`, serializers, timezone, retry/reliability policy, task auto-discovery. |
| `worker.py` | Process entry point. Sets up structured logging for the worker process and explicitly imports task modules. This is what `-A` points at. |
| `tasks.py` | The actual pipeline tasks, plus the shared `log_task_execution` logging decorator every task uses. |
| `scheduler.py` | Placeholder periodic (Celery Beat) tasks for future maintenance jobs. Not on an active schedule yet. |

Nothing outside this package defines a Celery task. Business logic
(what a task *does*) lives in the existing service layer —
`app.modules.assets.processing.pipeline.AssetProcessingService`,
`app.modules.assets.processing.extractors`,
`app.modules.knowledge_base.embeddings` — tasks here are thin bridges
into it, since Celery's default `prefork` pool doesn't support native
`async def` task bodies.

## Tasks

| Task | Status | Notes |
|---|---|---|
| `workers.process_uploaded_asset` | **Live** | The one actually enqueued, by `AssetService.upload` and `POST /assets/{id}/process`. Runs extract -> chunk -> (Sprint 9B) local Qwen document understanding, inline. |
| `workers.extract_document_text` | Standalone, not yet wired to any caller | Extraction only, in isolation — for future composition (e.g. a Celery `chain()`). |
| `workers.generate_ai_metadata` | **Live**, standalone | Sprint 9B. Runs local Qwen document understanding (via Ollama) for one asset in isolation, sharing `QwenDocumentUnderstandingService` with the inline pipeline call above — not a duplicate implementation. Requires extraction to have already produced text. |
| `workers.generate_embeddings` | Live, standalone | Embeds all existing asset chunks, validates vectors and records the full configured space identity. |
| `workers.update_processing_status` | Standalone, not yet wired to any caller | Generic status-update utility, usable outside the main pipeline. |
| `workers.execute_research_run` | **Live** | Sprint 7. Enqueued by `ResearchService.start_run` (`POST /research/run`). Executes the LangGraph research workflow and writes the run's step/message trace. |
| `workers.retry_failed_tasks` | Real, manual-invoke only | Queries assets stuck at `processing_status=FAILED` and re-enqueues them. Not on a beat schedule. |
| `workers.cleanup_temp_files` | Placeholder | No "temp file" concept exists yet in the storage layer. |
| `workers.rebuild_embeddings` | Live, resumable | Takes a rebuild UUID created by `POST /knowledge-base/rebuild?project_id=…`; stages validated batches, retries unfinished work, and atomically activates a complete project space. |

## Running the worker

```bash
# Inside the backend container (Docker Compose service `worker`)
celery -A app.workers.worker worker --loglevel=info

# From the repo, via Compose
docker compose up worker
```

## Reliability policy

- `task_acks_late=True` + `task_reject_on_worker_lost=True`: a task is
  only removed from the broker after it *finishes*. If a worker is
  killed mid-task, the task is redelivered rather than lost. This is
  safe here because the pipeline is idempotent — reprocessing an asset
  replaces its chunks rather than duplicating them.
- Each task is `bind=True` with `max_retries`/`default_retry_delay`,
  retrying on unexpected exceptions (transient DB/network failures).
  `extract_document_text` is the one exception: an
  `ExtractionNotSupportedError` (unsupported MIME type) is *not*
  retried, since retrying can't turn an unsupported format into a
  supported one.
- For `process_uploaded_asset` specifically, `AssetProcessingService`
  already records pipeline failures on the asset itself
  (`processing_status=FAILED`, `processing_error=...`) rather than
  raising — so that task's retry handling is a secondary safety net
  for infrastructure-level failures (e.g. the DB being unreachable),
  not the primary error-reporting path. The asset row is the source of
  truth for "did processing succeed," queryable via `GET
  /assets/{id}` without polling a Celery result.

## Logging

Every task is wrapped by `log_task_execution` (`tasks.py`), which logs
via the app's existing structured logger
(`app.core.logging.logger.get_logger`):

- `task_started` — task name, task id, `target_id` (the task's first argument: an asset id for pipeline tasks, a run id for research execution)
- `task_completed` — task name, task id, `duration_seconds`
- `task_failed` — task name, task id, `duration_seconds`, `error_type`, `error_message`

## Not yet done (by design)

- No `beat_schedule` is configured — `scheduler.py` tasks exist and
  are invokable but don't run automatically. Wiring up a real cadence
  is a future decision, not made here.
- No real OCR, document parsing beyond plain text, or embedding
  provider. See `app.modules.assets.processing.extractors` and
  `app.modules.knowledge_base.embeddings` for the abstraction points
  future sprints implement against.
