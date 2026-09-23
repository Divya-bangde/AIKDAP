"""Workflow timeline capture (Phase 1a): the instrument wrapper, per-step
LLM usage, step metadata, Redis publishing, and persistence of step
rows when a workflow raises."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.agents.planner.registry import NodeSpec
from app.agents.planner.tracking import TRACKER_CONFIG_KEY, NodeExecutionTracker, instrument
from app.core.llm.usage import current_step_usage, record_llm_usage
from app.modules.research.enums import ResearchRunStatus, ResearchStepStatus
from app.modules.research.models import ResearchRun, ResearchStep
from app.modules.research.repository import ResearchStepRepository
from app.modules.research.service import ResearchStepTracker
from app.modules.research.step_events import (
    MAX_STEP_ERROR_CHARS,
    StepEventPublisher,
    own_session_factory,
    step_metadata,
)


class RecordingTracker(NodeExecutionTracker):
    def __init__(self) -> None:
        self.events: list[tuple[str, ...]] = []
        self.usage_seen: list[Any] = []

    async def on_node_start(self, node: str) -> None:
        self.events.append(("start", node))

    async def on_node_success(self, node: str, update: dict[str, Any], duration_ms: int) -> None:
        self.usage_seen.append(current_step_usage())
        self.events.append(("success", node))

    async def on_node_failure(self, node, error, duration_ms, critical) -> None:
        self.usage_seen.append(current_step_usage())
        self.events.append(("failure", node, type(error).__name__))


def _spec(handler, *, critical: bool = True) -> NodeSpec:
    return NodeSpec(name="planner", title="Plan", handler=handler, critical=critical, description="")


def _config(tracker: NodeExecutionTracker) -> dict[str, Any]:
    return {"configurable": {TRACKER_CONFIG_KEY: tracker}}


def _two_calls() -> None:
    record_llm_usage(provider="groq", model="groq/a", input_tokens=10, output_tokens=5)
    record_llm_usage(provider="gemini", model="gemini/b", input_tokens=3, output_tokens=None)


@pytest.mark.asyncio
async def test_async_node_success_tracks_and_sums_usage():
    async def handler(state, config):
        _two_calls()
        return {"objective": "x"}

    tracker = RecordingTracker()
    assert await instrument(_spec(handler))({}, _config(tracker)) == {"objective": "x"}
    assert tracker.events == [("start", "planner"), ("success", "planner")]
    [usage] = tracker.usage_seen
    assert (usage.provider, usage.model) == ("gemini", "gemini/b")  # last model
    assert (usage.input_tokens, usage.output_tokens) == (13, 5)
    assert usage.models == ["groq/a", "gemini/b"]
    assert current_step_usage() is None  # scope closed after the node


@pytest.mark.asyncio
async def test_sync_node_is_supported():
    def handler(state, config):
        return {"objective": "sync"}

    tracker = RecordingTracker()
    assert await instrument(_spec(handler))({}, _config(tracker)) == {"objective": "sync"}
    assert tracker.events[-1] == ("success", "planner")
    assert tracker.usage_seen[0].model is None  # no LLM call -> fields stay null


@pytest.mark.asyncio
async def test_critical_failure_is_recorded_then_reraised():
    async def handler(state, config):
        _two_calls()
        raise RuntimeError("boom")

    tracker = RecordingTracker()
    with pytest.raises(RuntimeError, match="boom"):
        await instrument(_spec(handler))({}, _config(tracker))
    assert tracker.events[-1] == ("failure", "planner", "RuntimeError")
    assert tracker.usage_seen[0].input_tokens == 13  # usage kept on failure too


@pytest.mark.asyncio
async def test_sync_non_critical_failure_degrades_instead_of_raising():
    def handler(state, config):
        raise ValueError("down")

    tracker = RecordingTracker()
    update = await instrument(_spec(handler, critical=False))({}, _config(tracker))
    assert update["intermediate_results"]["planner_failure"]["error_type"] == "ValueError"
    assert tracker.events[-1] == ("failure", "planner", "ValueError")


def test_step_metadata_for_retrieval_decision_and_web():
    docs = [{"rerank_score": 0.4}, {"retrieval_score": 0.91}, {"score": "n/a"}]
    assert step_metadata("asset_retrieval", {"retrieved_documents": docs}) == {
        "chunks_found": 3,
        "top_score": 0.91,
    }
    assert step_metadata("web_research", {"retrieved_documents": docs[:2]}) == {"results_count": 2}
    decided = step_metadata("synthesis", {"web_fallback": False, "grounding_status": "grounded"})
    assert decided["used_web"] is False
    assert decided["reason"] == "Found enough in your documents."
    assert step_metadata("synthesis", {"web_fallback": True})["used_web"] is True
    general = step_metadata("synthesis", {"web_fallback": False, "grounding_status": "unsourced"})
    assert "general knowledge" in general["reason"]
    assert step_metadata("context_builder", {}) == {}


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.messages: list[tuple[str, dict]] = []
        self.fail = fail

    async def publish(self, channel: str, message: str) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.messages.append((channel, json.loads(message)))


def _step(**overrides) -> ResearchStep:
    values = dict(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        attempt=1,
        step_index=2,
        node_name="synthesis",
        title="Synthesize",
        status=ResearchStepStatus.COMPLETED,
        duration_ms=40,
        model_name="groq/a",
        step_metadata={"used_web": False},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    values.update(overrides)
    return ResearchStep(**values)


@pytest.mark.asyncio
async def test_publisher_sends_the_event_json():
    redis = FakeRedis()
    step = _step()
    await StepEventPublisher("run:1:steps", client=redis).publish(step)
    [(channel, payload)] = redis.messages
    assert channel == "run:1:steps"
    assert payload["node_name"] == "synthesis"
    assert payload["status"] == "completed"
    assert payload["step_index"] == 2
    assert payload["model_name"] == "groq/a"
    assert payload["metadata"] == {"used_web": False}


@pytest.mark.asyncio
async def test_publisher_failure_never_raises():
    await StepEventPublisher("run:1:steps", client=FakeRedis(fail=True)).publish(_step())


# --- persistence against the real database (see conftest) -------------------


async def _run(session, project) -> ResearchRun:
    run = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query="q",
        status=ResearchRunStatus.RUNNING,
        include_assets=False,
        include_web=False,
        max_results=3,
    )
    session.add(run)
    await session.commit()
    return run


@pytest.mark.asyncio
async def test_step_rows_persist_when_the_workflow_raises(session, project):
    """Own-session writes: the failed step and the earlier completed one
    survive a workflow whose own transaction is rolled back."""
    run = await _run(session, project)
    redis = FakeRedis()
    tracker = ResearchStepTracker(
        session,
        run.id,
        session_factory=own_session_factory(),
        publisher=StepEventPublisher("t", client=redis),
    )

    async def ok(state, config):
        record_llm_usage(provider="groq", model="groq/a", input_tokens=7, output_tokens=2)
        return {"step": {"summary": "planned"}}

    async def broken(state, config):
        raise RuntimeError("x" * 5000)

    config = _config(tracker)
    await instrument(_spec(ok))({}, config)
    router = NodeSpec(name="router", title="Route", handler=broken, critical=True, description="")
    with pytest.raises(RuntimeError):
        await instrument(router)({}, config)
    # The planner node runs again (a loop): a new row, never an overwrite.
    await instrument(_spec(ok))({}, config)
    await session.rollback()  # the workflow's own transaction is discarded

    steps = await ResearchStepRepository(session).list_by_run(run.id)
    assert [(s.step_index, s.node_name, s.status) for s in steps] == [
        (0, "planner", ResearchStepStatus.COMPLETED),
        (1, "router", ResearchStepStatus.FAILED),
        (2, "planner", ResearchStepStatus.COMPLETED),
    ]
    assert (steps[0].model_name, steps[0].input_tokens, steps[0].output_tokens) == ("groq/a", 7, 2)
    assert steps[0].step_metadata["models"] == ["groq/a"]
    assert len(steps[1].error_message) == MAX_STEP_ERROR_CHARS
    # running + finished event per execution, in order.
    assert [(m[1]["node_name"], m[1]["status"]) for m in redis.messages] == [
        ("planner", "running"),
        ("planner", "completed"),
        ("router", "running"),
        ("router", "failed"),
        ("planner", "running"),
        ("planner", "completed"),
    ]
