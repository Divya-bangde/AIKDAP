"""Registry of the report graph's nodes -- mirrors
`agents.planner.registry`, reusing its `NodeSpec` rather than
redefining an equivalent dataclass. Every node here is `critical=True`:
a report has no useful partial output, so any node's failure must abort
the whole run (spec section 7)."""

from app.agents.planner.registry import NodeSpec
from app.agents.reports.nodes import collect_documents_node, coverage_check_node, write_sections_node
from app.agents.reports.state import ReportNode

REPORT_AGENT_REGISTRY: dict[str, NodeSpec] = {
    ReportNode.COLLECT_DOCUMENTS.value: NodeSpec(
        name=ReportNode.COLLECT_DOCUMENTS.value,
        title="Collect project documents",
        handler=collect_documents_node,
        critical=True,
        description="Gathers every processed document's AI-profile summary and topics.",
    ),
    ReportNode.WRITE_SECTIONS.value: NodeSpec(
        name=ReportNode.WRITE_SECTIONS.value,
        title="Write report sections",
        handler=write_sections_node,
        critical=True,
        description="Retrieves evidence and writes each section, one LLM call per section.",
    ),
    ReportNode.COVERAGE_CHECK.value: NodeSpec(
        name=ReportNode.COVERAGE_CHECK.value,
        title="Check coverage",
        handler=coverage_check_node,
        critical=True,
        description="Builds the References section and records final coverage.",
    ),
}


def get_node_spec(name: str) -> NodeSpec:
    """Look up one node's spec by name."""
    try:
        return REPORT_AGENT_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown report node '{name}'. Registered: {', '.join(sorted(REPORT_AGENT_REGISTRY))}."
        ) from exc
