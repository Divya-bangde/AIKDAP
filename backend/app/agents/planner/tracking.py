"""Per-node execution tracking and failure handling for the graph.

Every registered agent is wrapped by `instrument()` before it becomes a
LangGraph node. The wrapper times the call, reports start/success/
failure to an injected `NodeExecutionTracker`, and applies the failure
policy the registry declares for that agent.

Why the wrapper exists rather than tracking from outside the graph: an
exception raised inside a node surfaces from `astream`/`ainvoke` with
no indication of *which* node produced it, so a caller cannot mark the
right `research_steps` row as failed. Instrumenting each node is the
only place that knowledge exists.

This module stays free of database and HTTP concerns. The tracker is an
abstract contract; `app.modules.research.service` supplies the
implementation that writes `research_steps` and `agent_messages`, and
`NullTracker` lets the graph run untracked in unit tests.
"""

import functools
import inspect
import time
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.planner.registry import NodeSpec
from app.agents.planner.state import ResearchState
from app.core.llm.usage import step_usage_scope
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

#: Config key the tracker is injected under, alongside `dependencies`.
TRACKER_CONFIG_KEY = "tracker"


class NodeExecutionTracker(ABC):
    """Receives the lifecycle of every node execution in one run.

    Implementations own their own step ordering: the graph reports
    events in execution order and does not assign indices, so a tracker
    is free to number, batch, or persist them however it needs.
    """

    @abstractmethod
    async def on_node_start(self, node: str) -> None:
        """A node is about to execute."""

    @abstractmethod
    async def on_node_success(
        self, node: str, update: dict[str, Any], duration_ms: int
    ) -> None:
        """A node completed and returned `update` as its state delta."""

    @abstractmethod
    async def on_node_failure(
        self, node: str, error: BaseException, duration_ms: int, critical: bool
    ) -> None:
        """A node raised. `critical` reflects the registry's policy."""


class NullTracker(NodeExecutionTracker):
    """Tracker that records nothing.

    The default when no tracker is injected, so the graph is runnable
    standalone — in a unit test, or from a future caller that has no
    database session.
    """

    async def on_node_start(self, node: str) -> None:
        return None

    async def on_node_success(
        self, node: str, update: dict[str, Any], duration_ms: int
    ) -> None:
        return None

    async def on_node_failure(
        self, node: str, error: BaseException, duration_ms: int, critical: bool
    ) -> None:
        return None


def get_tracker(config: RunnableConfig) -> NodeExecutionTracker:
    """Extract the injected tracker, falling back to a no-op."""
    tracker = (config.get("configurable") or {}).get(TRACKER_CONFIG_KEY)
    if isinstance(tracker, NodeExecutionTracker):
        return tracker
    return NullTracker()


def instrument(spec: NodeSpec):
    """Wrap an agent's handler with tracking and its failure policy.

    On success the handler's own state update is returned untouched —
    the wrapper never rewrites a node's output.

    On failure:

    - the tracker records the step as failed, with the error;
    - a critical node re-raises, aborting the graph so the run fails
      rather than producing an answer from a broken pipeline;
    - a non-critical node returns a degraded-mode marker under
      `intermediate_results`, letting the workflow continue with the
      evidence it does have. Synthesis reads those markers and states
      in the answer that the evidence is incomplete.

    Exceptions are never swallowed: a non-critical failure is recorded,
    logged at error level, and surfaced in the final answer.
    """

    @functools.wraps(spec.handler)
    async def instrumented(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
        # One usage accumulator per execution (a retry or loop gets a
        # fresh one). The tracker callbacks run inside the scope so they
        # can read what the gateway recorded for this step.
        with step_usage_scope():
            return await _run_instrumented(spec, state, config)

    return instrumented


async def _call_handler(spec: NodeSpec, state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
    """Call a node handler that may be sync or async."""
    result = spec.handler(state, config)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _run_instrumented(
    spec: NodeSpec, state: ResearchState, config: RunnableConfig
) -> dict[str, Any]:
    tracker = get_tracker(config)
    run_id = state.get("run_id")
    start = time.monotonic()

    await tracker.on_node_start(spec.name)
    logger.info("research_node_started", run_id=run_id, node=spec.name)

    try:
        update = await _call_handler(spec, state, config)
    except Exception as exc:
        duration_ms = _elapsed_ms(start)
        await tracker.on_node_failure(spec.name, exc, duration_ms, spec.critical)
        logger.error(
            "research_node_failed",
            run_id=run_id,
            node=spec.name,
            status="failed",
            critical=spec.critical,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        if spec.critical:
            raise
        return {
            "intermediate_results": {
                f"{spec.name}_failure": {
                    "node": spec.name,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "critical": False,
                }
            }
        }

    duration_ms = _elapsed_ms(start)
    await tracker.on_node_success(spec.name, update, duration_ms)
    logger.info(
        "research_node_completed",
        run_id=run_id,
        node=spec.name,
        status="completed",
        duration_ms=duration_ms,
    )
    return update


def _elapsed_ms(start: float) -> int:
    """Milliseconds elapsed since a monotonic start marker."""
    return int((time.monotonic() - start) * 1000)
