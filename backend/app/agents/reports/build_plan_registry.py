"""Registry of the build-plan graph's nodes -- mirrors
`agents.reports.registry`, reusing `agents.planner.registry.NodeSpec`.

Unlike the synopsis registry (every node critical), `research_further`
is `critical=False`: OpenAlex and Tavily are external services, and the
spec requires the report to complete on whatever is available when one
is missing a key or fails (spec sections 6 and 7).
"""

from app.agents.planner.registry import NodeSpec
from app.agents.reports.build_plan_nodes import (
    extract_node,
    recommend_process_node,
    recommend_tools_node,
    research_further_node,
)
from app.agents.reports.build_plan_state import BuildPlanNode

BUILD_PLAN_AGENT_REGISTRY: dict[str, NodeSpec] = {
    BuildPlanNode.EXTRACT.value: NodeSpec(
        name=BuildPlanNode.EXTRACT.value,
        title="Extract what the paper builds",
        handler=extract_node,
        critical=True,
        description="Reads method, models, datasets, metrics, compute needs and limitations from the selected papers.",
    ),
    BuildPlanNode.RESEARCH_FURTHER.value: NodeSpec(
        name=BuildPlanNode.RESEARCH_FURTHER.value,
        title="Research further",
        handler=research_further_node,
        critical=False,
        description="Searches OpenAlex for follow-up methods and Tavily for implementations, libraries and datasets.",
    ),
    BuildPlanNode.RECOMMEND_TOOLS.value: NodeSpec(
        name=BuildPlanNode.RECOMMEND_TOOLS.value,
        title="Recommend tools",
        handler=recommend_tools_node,
        critical=True,
        description="Recommends tools per build stage, each with a reason from the paper and alternatives.",
    ),
    BuildPlanNode.RECOMMEND_PROCESS.value: NodeSpec(
        name=BuildPlanNode.RECOMMEND_PROCESS.value,
        title="Recommend a build process",
        handler=recommend_process_node,
        critical=True,
        description="Lays out phases from reproducing the baseline to a working product, with done criteria and risks.",
    ),
}


def get_build_plan_node_spec(name: str) -> NodeSpec:
    """Look up one build-plan node's spec by name."""
    try:
        return BUILD_PLAN_AGENT_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown build-plan node '{name}'. "
            f"Registered: {', '.join(sorted(BUILD_PLAN_AGENT_REGISTRY))}."
        ) from exc
