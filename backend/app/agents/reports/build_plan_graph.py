"""The build-plan LangGraph orchestrator: a strictly linear graph
(spec section 6) -- extract, research further, recommend tools,
recommend a process, done. No conditional edges and no loop-back, the
same shape `agents.reports.graph` uses for the synopsis.

Reuses `agents.planner.tracking.instrument`, so each node gets the same
timing/logging/failure-policy wrapper -- which is also what makes
`research_further`'s `critical=False` take effect without any
build-plan-specific error handling in the graph itself.
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.planner.tracking import instrument
from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.agents.reports.build_plan_state import BuildPlanNode, BuildPlanState


def build_build_plan_graph() -> StateGraph:
    """Construct the uncompiled build-plan graph from its registry."""
    builder: StateGraph = StateGraph(BuildPlanState)

    for name, spec in BUILD_PLAN_AGENT_REGISTRY.items():
        builder.add_node(name, instrument(spec))

    builder.add_edge(START, BuildPlanNode.EXTRACT.value)
    builder.add_edge(BuildPlanNode.EXTRACT.value, BuildPlanNode.RESEARCH_FURTHER.value)
    builder.add_edge(BuildPlanNode.RESEARCH_FURTHER.value, BuildPlanNode.RECOMMEND_TOOLS.value)
    builder.add_edge(BuildPlanNode.RECOMMEND_TOOLS.value, BuildPlanNode.RECOMMEND_PROCESS.value)
    builder.add_edge(BuildPlanNode.RECOMMEND_PROCESS.value, END)

    return builder


@lru_cache(maxsize=1)
def get_build_plan_graph() -> CompiledStateGraph:
    """Return the process-wide compiled build-plan graph, compiled once
    and reused -- it carries no run-specific state, the same reasoning
    `agents.reports.graph.get_report_graph` documents."""
    return build_build_plan_graph().compile()
