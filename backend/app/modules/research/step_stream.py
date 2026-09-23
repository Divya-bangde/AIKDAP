"""Server-Sent Events stream of a run's workflow steps (timeline, Phase 2).

One generator serves research runs and report runs; the caller supplies
the Redis channel and a `load` function that returns the current step
rows and whether the run has reached a terminal status.

Protocol (`text/event-stream`):

- `event: step` / `data: <ResearchStepRead JSON>` — a step that is new
  or changed since it was last sent. The client merges by `id`.
- `: heartbeat` comment every `HEARTBEAT_SECONDS`, so proxies keep the
  connection open.
- `event: end` / `data: {"reason": "terminal" | "timeout"}` — the run
  finished (or the stream hit its cap); the server then closes.

Delivery combines Redis pub/sub (instant) with a short database tick
(authoritative). The tick sends the existing rows first, covers any
event published between that read and the subscription, picks up rows
written without an event (steps recorded as skipped at the end of a
run), detects the terminal status, and keeps the stream working if
Redis is down -- in which case updates just arrive a tick later.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging.logger import get_logger
from app.database.session import open_session
from app.modules.research.models import ResearchStep
from app.modules.research.step_events import step_event_payload

logger = get_logger(__name__)

HEARTBEAT_SECONDS = 15.0
TICK_SECONDS = 2.0
#: A stream never outlives this, even for a run stuck in `running`; the
#: client then falls back to polling.
MAX_STREAM_SECONDS = 30 * 60

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}

StepLoader = Callable[[AsyncSession], Awaitable[tuple[list[ResearchStep], bool]]]


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _version(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (payload.get("status"), payload.get("completed_at"), payload.get("duration_ms"))


async def _subscribe(channel: str) -> tuple[Any, Any] | tuple[None, None]:
    try:
        import redis.asyncio as redis

        client = redis.from_url(
            settings.celery_broker_url,
            socket_connect_timeout=settings.step_events_redis_timeout,
        )
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        return client, pubsub
    except Exception as exc:  # noqa: BLE001 - the DB tick still delivers
        logger.warning("step_stream_subscribe_failed", channel=channel, error_type=type(exc).__name__)
        return None, None


async def step_event_stream(
    *,
    channel: str,
    load: StepLoader,
    tick_seconds: float = TICK_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> AsyncIterator[str]:
    sent: dict[str, tuple[Any, ...]] = {}
    client, pubsub = await _subscribe(channel)
    started = last_write = time.monotonic()

    def fresh(payload: dict[str, Any]) -> bool:
        key = str(payload.get("id"))
        if sent.get(key) == _version(payload):
            return False
        sent[key] = _version(payload)
        return True

    try:
        while True:
            async with open_session() as session:
                steps, terminal = await load(session)
            for step in sorted(steps, key=lambda s: (s.attempt, s.step_index)):
                payload = step_event_payload(step)
                if fresh(payload):
                    last_write = time.monotonic()
                    yield _event("step", payload)
            if terminal:
                yield _event("end", {"reason": "terminal"})
                return
            if time.monotonic() - started > max_seconds:
                yield _event("end", {"reason": "timeout"})
                return

            deadline = time.monotonic() + tick_seconds
            while (remaining := deadline - time.monotonic()) > 0:
                message = None
                if pubsub is not None:
                    try:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=remaining
                        )
                    except Exception as exc:  # noqa: BLE001 - degrade to ticks only
                        logger.warning("step_stream_redis_lost", error_type=type(exc).__name__)
                        pubsub = None
                else:
                    await asyncio.sleep(remaining)
                if message and message.get("type") == "message":
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if fresh(payload):
                        last_write = time.monotonic()
                        yield _event("step", payload)
                if time.monotonic() - last_write >= heartbeat_seconds:
                    last_write = time.monotonic()
                    yield ": heartbeat\n\n"
    finally:
        if pubsub is not None:
            try:
                await pubsub.aclose()
            except Exception:  # noqa: BLE001 - closing a dead connection
                pass
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
