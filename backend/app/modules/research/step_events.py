"""Shared plumbing for the `research_steps` trackers (workflow timeline).

Both `research.service.ResearchStepTracker` and
`reports.tracking.ReportStepTracker` write one `research_steps` row per
node execution. This module holds what they have in common so neither
duplicates it:

- `step_write_scope` — where a step write happens. In production each
  write uses its own short-lived session and commits immediately, so a
  failing workflow keeps its events and the event writes never share a
  transaction with the workflow. Passing no factory keeps the legacy
  behaviour of writing on the workflow's session (used by unit tests
  that inspect rows through that same session).
- `apply_step_usage` / `step_metadata` — the model, token and
  display-metadata fields recorded on each row.
- `StepEventPublisher` — best-effort JSON publish to Redis after every
  write, for the live timeline stream.
"""

import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm.usage import current_step_usage
from app.core.logging.logger import get_logger
from app.modules.research.models import ResearchStep

logger = get_logger(__name__)

#: Longest error text stored on a step row.
MAX_STEP_ERROR_CHARS = 2000

SessionFactory = Callable[[], AsyncSession]


def own_session_factory() -> SessionFactory:
    """The factory production trackers write step events with."""
    from app.database.session import open_session

    return open_session


@asynccontextmanager
async def step_write_scope(
    shared_session: AsyncSession, factory: SessionFactory | None
) -> AsyncIterator[AsyncSession]:
    """Yield the session a step write should use, committing on exit."""
    if factory is None:
        yield shared_session
        await shared_session.commit()
        return
    async with factory() as session:
        yield session
        await session.commit()


def truncate_error(message: str) -> str:
    return message[:MAX_STEP_ERROR_CHARS]


def apply_step_usage(step: ResearchStep) -> None:
    """Copy the running step's LLM usage onto its row."""
    usage = current_step_usage()
    if usage is None or usage.model is None:
        return
    step.model_provider = usage.provider
    step.model_name = usage.model
    step.input_tokens = usage.input_tokens
    step.output_tokens = usage.output_tokens
    step.step_metadata = {**(step.step_metadata or {}), "models": list(usage.models)}


def _top_score(documents: list[dict[str, Any]]) -> float | None:
    scores = [
        score
        for doc in documents
        for score in [doc.get("rerank_score", doc.get("retrieval_score", doc.get("score")))]
        if isinstance(score, (int, float))
    ]
    return round(max(scores), 4) if scores else None


def step_metadata(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """Small display facts derived from a node's state update.

    Derived here, from the update the node already returns, so no node
    body changes to feed the timeline.
    """
    documents = update.get("retrieved_documents") or []
    if node == "asset_retrieval":
        return {"chunks_found": len(documents), "top_score": _top_score(documents)}
    if node == "web_research":
        return {"results_count": len(documents)}
    if node == "router":
        selected = update.get("selected_agents") or []
        used_web = "web_research" in selected and "asset_retrieval" not in selected
        return {
            "used_web": used_web,
            "reason": (
                "No project documents were selected, so the web is the primary source."
                if used_web
                else "Your project documents are searched first."
            ),
        }
    if node == "synthesis" and "web_fallback" in update:
        used_web = bool(update.get("web_fallback"))
        grounding = update.get("grounding_status")
        if used_web:
            reason = "Your documents did not contain enough evidence, so the web is searched next."
        elif grounding == "unsourced":
            reason = "Your documents did not answer this, so it was answered from general knowledge."
        elif grounding == "insufficient_evidence":
            reason = "Not enough evidence was found and no further web search was available."
        else:
            reason = "Found enough in your documents."
        return {"used_web": used_web, "reason": reason, "grounding_status": grounding}
    return {}


def step_event_payload(step: ResearchStep) -> dict[str, Any]:
    """The JSON shape of one step event: exactly `ResearchStepRead`, so
    the list API and the live stream deliver the same object."""
    from app.modules.research.schemas import ResearchStepRead

    return ResearchStepRead.model_validate(step).model_dump(mode="json")


def run_steps_channel(run_id: Any) -> str:
    return f"run:{run_id}:steps"


def report_steps_channel(asset_id: Any) -> str:
    return f"report:{asset_id}:steps"


class StepEventPublisher:
    """Publishes step events to one Redis channel, best-effort.

    A live stream is a convenience on top of the persisted rows, so a
    Redis problem never fails a step. After a failure, publishing is
    paused process-wide for `_BACKOFF_SECONDS` instead of paying a
    connect timeout on every step of every run; the timeline still
    loads from the database, and clients fall back to polling.
    """

    _BACKOFF_SECONDS = 30.0
    #: Monotonic time before which no publish is attempted.
    _paused_until = 0.0

    def __init__(self, channel: str, *, client: Any | None = None) -> None:
        self._channel = channel
        self._client = client

    async def publish(self, step: ResearchStep) -> None:
        if self._client is None and time.monotonic() < StepEventPublisher._paused_until:
            return
        try:
            message = json.dumps(step_event_payload(step))
            if self._client is not None:
                await self._client.publish(self._channel, message)
                return
            import redis.asyncio as redis

            # A connection per event rather than one held for the run:
            # Celery runs each task on a fresh event loop, and a client
            # outliving its loop fails on close. A handful of steps per
            # run makes the extra connect negligible.
            async with redis.from_url(
                settings.celery_broker_url,
                socket_connect_timeout=settings.step_events_redis_timeout,
                socket_timeout=settings.step_events_redis_timeout,
            ) as client:
                await client.publish(self._channel, message)
        except Exception as exc:  # noqa: BLE001 - the stream is best-effort
            StepEventPublisher._paused_until = time.monotonic() + self._BACKOFF_SECONDS
            logger.warning(
                "step_event_publish_failed",
                channel=self._channel,
                error_type=type(exc).__name__,
            )
