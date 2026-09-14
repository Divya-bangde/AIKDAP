"""Registry of the agents (graph nodes) the research workflow can run.

One place that answers "what agents exist, what does each one do, and
what happens when one fails". The orchestrator builds the graph from
this registry rather than from a hardcoded list of `add_node` calls, so
adding an agent is a registration plus its edges — not an edit to the
workflow builder.

Deliberately small: a dict of specs and two lookup helpers. There is no
plugin discovery, no dependency-injection container, and no lifecycle
protocol, because nothing in the platform needs them yet. Runtime
dependencies (the planner strategy, retrievers, the synthesizer) are
already injected per-run through the graph config; the registry only
describes *which* nodes exist and how the graph should treat them.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.planner.nodes import (
    asset_retrieval_node,
    context_builder_node,
    paper_suggestion_node,
    planner_node,
    router_node,
    synthesis_node,
    web_research_node,
)
from app.agents.planner.state import RETRIEVAL_NODES, ResearchNode, ResearchState

#: A node handler: takes the shared state plus the graph config and
#: returns a partial state update. Matches the LangGraph node contract.
NodeHandler = Callable[[ResearchState, RunnableConfig], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class NodeSpec:
    """Everything the orchestrator needs to know about one agent.

    `critical` encodes the failure policy declaratively, next to the
    agent it describes, rather than as a conditional buried in the
    execution service:

    - `critical=True` — the run cannot produce a meaningful result
      without this node. Its failure aborts the graph and fails the run
      (e.g. without a planner there is nothing to execute).
    - `critical=False` — the run can continue with less evidence. The
      failure is recorded on the step, surfaced to synthesis as a
      degraded-mode warning, and execution proceeds (e.g. an
      unreachable web provider should not discard the knowledge-base
      evidence already gathered).
    """

    name: str
    title: str
    handler: NodeHandler
    critical: bool
    description: str

    @property
    def is_retrieval(self) -> bool:
        """Whether the router may route around this agent.

        Derived from `RETRIEVAL_NODES` rather than from `critical`:
        routability and criticality are separate concerns that happen
        to coincide today, and a future non-critical agent need not be
        a retrieval agent.
        """
        return self.name in {node.value for node in RETRIEVAL_NODES}


#: The agents available to the research workflow, in graph order.
#:
#: `planner`, `router`, `context_builder`, and `synthesis` form the
#: workflow's spine and always run. `web_research` and `asset_retrieval`
#: are the routable retrieval agents — the router decides which of them
#: execute, and the rest are recorded as skipped.
AGENT_REGISTRY: dict[str, NodeSpec] = {
    ResearchNode.PLANNER.value: NodeSpec(
        name=ResearchNode.PLANNER.value,
        title="Plan the research run",
        handler=planner_node,
        critical=True,
        description="Decomposes the request into an ordered execution plan.",
    ),
    ResearchNode.ROUTER.value: NodeSpec(
        name=ResearchNode.ROUTER.value,
        title="Route to retrieval agents",
        handler=router_node,
        critical=True,
        description="Translates the plan into the set of agents to execute.",
    ),
    ResearchNode.ASSET_RETRIEVAL.value: NodeSpec(
        name=ResearchNode.ASSET_RETRIEVAL.value,
        title="Search the project knowledge base",
        handler=asset_retrieval_node,
        critical=False,
        description="Retrieves evidence from the project's own processed assets.",
    ),
    ResearchNode.WEB_RESEARCH.value: NodeSpec(
        name=ResearchNode.WEB_RESEARCH.value,
        title="Gather external references",
        handler=web_research_node,
        critical=False,
        description="Retrieves evidence from external sources.",
    ),
    ResearchNode.CONTEXT_BUILDER.value: NodeSpec(
        name=ResearchNode.CONTEXT_BUILDER.value,
        title="Build the working context",
        handler=context_builder_node,
        critical=True,
        description="Merges and de-duplicates all evidence into one cited context.",
    ),
    ResearchNode.SYNTHESIS.value: NodeSpec(
        name=ResearchNode.SYNTHESIS.value,
        title="Synthesize the deliverable",
        handler=synthesis_node,
        critical=True,
        description="Produces the final answer with structured citations.",
    ),
    ResearchNode.PAPER_SUGGESTION.value: NodeSpec(
        name=ResearchNode.PAPER_SUGGESTION.value,
        title="Suggest supporting papers",
        handler=paper_suggestion_node,
        critical=False,
        description=(
            "Searches OpenAlex for papers that could fill an evidence gap "
            "in the answer. Never contributes evidence to synthesis."
        ),
    ),
}


def get_node_spec(name: str) -> NodeSpec:
    """Look up one agent's spec by node name."""
    try:
        return AGENT_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown agent '{name}'. Registered agents: "
            f"{', '.join(sorted(AGENT_REGISTRY))}."
        ) from exc


def registered_agents() -> list[str]:
    """The registered agent names, in graph order."""
    return list(AGENT_REGISTRY)


def retrieval_agents() -> list[str]:
    """The routable retrieval agents, in graph order."""
    return [name for name, spec in AGENT_REGISTRY.items() if spec.is_retrieval]


def register_agent(spec: NodeSpec) -> None:
    """Register an additional agent.

    The extension point for future sprints. Registering an agent makes
    the orchestrator instrument and add it as a node; wiring its edges
    remains an explicit decision in `graph.py`, because only the
    workflow knows where a new capability belongs in the sequence.
    """
    if spec.name in AGENT_REGISTRY:
        raise ValueError(f"Agent '{spec.name}' is already registered.")
    AGENT_REGISTRY[spec.name] = spec
