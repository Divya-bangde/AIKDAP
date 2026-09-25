"""The analytics LangGraph orchestrator: strictly linear, like the report graph."""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.analytics.registry import ANALYTICS_AGENT_REGISTRY
from app.agents.analytics.state import AnalyticsState
from app.agents.planner.tracking import instrument


def build_analytics_graph() -> StateGraph:
    builder: StateGraph = StateGraph(AnalyticsState)
    order = list(ANALYTICS_AGENT_REGISTRY)
    for name in order:
        builder.add_node(name, instrument(ANALYTICS_AGENT_REGISTRY[name]))
    for source, target in zip([START, *order], [*order, END]):
        builder.add_edge(source, target)
    return builder


@lru_cache(maxsize=1)
def get_analytics_graph() -> CompiledStateGraph:
    return build_analytics_graph().compile()
