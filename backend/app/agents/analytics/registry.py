"""Registry of the analytics graph's nodes (mirrors `agents.reports.registry`).
Only `explain` is non-critical: without a narrative the computed table
and chart are still a complete answer."""

from app.agents.analytics.nodes import (
    execute_node,
    explain_node,
    load_dataset_node,
    plan_analysis_node,
    validate_node,
)
from app.agents.analytics.state import AnalyticsNode
from app.agents.planner.registry import NodeSpec

ANALYTICS_AGENT_REGISTRY: dict[str, NodeSpec] = {
    AnalyticsNode.LOAD_DATASET.value: NodeSpec(
        name=AnalyticsNode.LOAD_DATASET.value, title="Load dataset", handler=load_dataset_node,
        critical=True, description="Reads the dataset and profiles its columns.",
    ),
    AnalyticsNode.PLAN_ANALYSIS.value: NodeSpec(
        name=AnalyticsNode.PLAN_ANALYSIS.value, title="Plan analysis", handler=plan_analysis_node,
        critical=True, description="Turns the question into a validated analysis plan.",
    ),
    AnalyticsNode.EXECUTE.value: NodeSpec(
        name=AnalyticsNode.EXECUTE.value, title="Compute result", handler=execute_node,
        critical=True, description="Runs the plan over the dataset.",
    ),
    AnalyticsNode.EXPLAIN.value: NodeSpec(
        name=AnalyticsNode.EXPLAIN.value, title="Explain result", handler=explain_node,
        critical=False, description="Writes a short explanation from the result only.",
    ),
    AnalyticsNode.VALIDATE.value: NodeSpec(
        name=AnalyticsNode.VALIDATE.value, title="Check numbers", handler=validate_node,
        critical=True, description="Checks every number in the explanation against the result.",
    ),
}
