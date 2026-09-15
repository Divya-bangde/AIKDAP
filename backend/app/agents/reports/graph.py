"""The report-generation LangGraph orchestrator: a small, strictly
linear graph (spec section 4) -- collect documents, write sections,
check coverage, done. No conditional edges, no loop-back: unlike the
research graph, a report either completes in one pass or the run fails.

Reuses `agents.planner.tracking.instrument` so every node gets the same
timing/logging/failure-policy wrapper the research graph's nodes get,
rather than reimplementing it here.
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.planner.tracking import instrument
from app.agents.reports.registry import REPORT_AGENT_REGISTRY
from app.agents.reports.state import ReportNode, ReportState


def build_report_graph() -> StateGraph:
    """Construct the uncompiled report graph from `REPORT_AGENT_REGISTRY`."""
    builder: StateGraph = StateGraph(ReportState)

    for name, spec in REPORT_AGENT_REGISTRY.items():
        builder.add_node(name, instrument(spec))

    builder.add_edge(START, ReportNode.COLLECT_DOCUMENTS.value)
    builder.add_edge(ReportNode.COLLECT_DOCUMENTS.value, ReportNode.WRITE_SECTIONS.value)
    builder.add_edge(ReportNode.WRITE_SECTIONS.value, ReportNode.COVERAGE_CHECK.value)
    builder.add_edge(ReportNode.COVERAGE_CHECK.value, END)

    return builder


@lru_cache(maxsize=1)
def get_report_graph() -> CompiledStateGraph:
    """Return the process-wide compiled report graph. Compiled once and
    reused -- the compiled graph carries no run-specific state, the
    same reasoning `agents.planner.graph.get_research_graph` documents."""
    return build_report_graph().compile()
