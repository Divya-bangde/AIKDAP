"""The Planner Agent: turns a research request into an execution plan.

`PlannerStrategy` is the abstraction point. This sprint ships one
implementation, `RuleBasedPlanner`, which is deterministic and calls no
model. It is not a stub — it performs real request classification and
produces a real, executable plan that the graph genuinely follows. A
future sprint adds an `LLMPlanner` alongside it and changes
`get_planner()`; no node, service, or route needs to change, because
they depend on the `PlannerStrategy` contract rather than on how the
plan was produced.

The plan is data, not control flow: the router reads it to decide the
route, so a different planner immediately produces different execution
without touching the graph topology.
"""

import re
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from app.agents.planner.state import RETRIEVAL_NODES, ResearchNode

#: Common English words carrying no retrieval signal. Small and
#: deliberate — an aggressive stop list would strip domain terms.
STOPWORDS: frozenset[str] = frozenset(
    {
        "about", "after", "against", "all", "also", "and", "any", "are", "because",
        "been", "before", "being", "between", "both", "but", "can", "could", "did",
        "does", "doing", "during", "each", "few", "for", "from", "further", "had",
        "has", "have", "having", "her", "here", "hers", "him", "his", "how",
        "into", "its", "itself", "just", "may", "me", "might", "more", "most",
        "must", "myself", "nor", "not", "now", "off", "once", "only", "other",
        "our", "ours", "out", "over", "own", "same", "should", "some", "such",
        "than", "that", "the", "their", "theirs", "them", "then", "there",
        "these", "they", "this", "those", "through", "too", "under", "until",
        "very", "was", "were", "what", "when", "where", "which", "while", "who",
        "whom", "why", "will", "with", "would", "you", "your", "yours",
    }
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9\-]*")

#: Request shapes the rule-based planner recognises, mapped to the
#: deliverable each one implies. Order matters: the first match wins.
_INTENT_SIGNALS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "comparison",
        # "differ" also matches "different"/"difference(s)" by substring.
        ("compare", "versus", "vs", "differ", "better", "against"),
        "a side-by-side comparison with the criteria made explicit",
    ),
    (
        "trend_analysis",
        ("trend", "trends", "forecast", "growth", "decline", "over time", "outlook"),
        "a trend analysis covering direction, magnitude, and drivers",
    ),
    (
        "summary",
        ("summarize", "summarise", "summary", "overview", "brief", "recap"),
        "a condensed summary of the material with the key points ranked",
    ),
    (
        "procedure",
        ("how to", "steps", "process", "implement", "workflow", "procedure"),
        "an ordered, actionable procedure",
    ),
    (
        "evaluation",
        ("risk", "risks", "impact", "evaluate", "assessment", "pros", "cons"),
        "an evidence-backed evaluation with the trade-offs stated",
    ),
)

_DEFAULT_INTENT = "open_research"
_DEFAULT_DELIVERABLE = "a grounded answer supported by the retrieved evidence"


def extract_keywords(text: str, *, limit: int = 12) -> list[str]:
    """Extract ranked retrieval keywords from free text.

    Frequency-ranked, stop-word filtered, ties broken alphabetically so
    the result is deterministic — a property the tests and the
    reproducibility of a stored run both depend on.
    """
    counts: dict[str, int] = {}
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if len(token) < 3 or token in STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
    return sorted(counts, key=lambda token: (-counts[token], token))[:limit]


class PlanRequest(BaseModel):
    """Everything the planner needs to produce a plan."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1)
    include_assets: bool = True
    include_web: bool = True
    max_results: int = Field(default=5, ge=1, le=20)


class PlanStep(BaseModel):
    """One executable step of the plan, naming the node that runs it."""

    index: int = Field(ge=0)
    node: ResearchNode
    title: str
    description: str


class ResearchPlan(BaseModel):
    """The planner's complete output.

    Persisted verbatim to `research_runs.plan`, so it doubles as the
    Explainable-AI record of *why* the run executed the way it did.
    """

    strategy: str
    intent: str
    objective: str
    rationale: str
    keywords: list[str]
    steps: list[PlanStep]

    @property
    def retrieval_nodes(self) -> list[ResearchNode]:
        """The retrieval steps in the plan, in execution order."""
        return [step.node for step in self.steps if step.node in RETRIEVAL_NODES]


class PlannerStrategy(ABC):
    """Contract for producing an execution plan from a request.

    The integration point for a real LLM-backed planner. Implementations
    must be free of persistence and HTTP concerns and must return a plan
    whose steps reference only real `ResearchNode` members — the graph
    dispatches on them directly.
    """

    #: Stable identifier recorded on every plan, so a stored run always
    #: says which planner produced it.
    name: str = "abstract"

    @abstractmethod
    async def plan(self, request: PlanRequest) -> ResearchPlan:
        """Produce an execution plan for the given request."""


class RuleBasedPlanner(PlannerStrategy):
    """Deterministic planner: classifies the request, then sequences the
    enabled retrieval sources before context building and synthesis.

    Ships in place of an LLM planner. Its decisions are real and drive
    real execution; what it lacks versus a model is nuance, not
    function.
    """

    name = "rule_based_v1"

    async def plan(self, request: PlanRequest) -> ResearchPlan:
        """Classify the request and build the ordered execution plan."""
        intent, deliverable = self._classify(request.query)
        keywords = extract_keywords(request.query)

        steps: list[PlanStep] = []

        # The project's own knowledge base is searched first: it is
        # owned, trusted, and already scoped to the user. External
        # sources only ever supplement it.
        if request.include_assets:
            steps.append(
                PlanStep(
                    index=len(steps),
                    node=ResearchNode.ASSET_RETRIEVAL,
                    title="Search the project knowledge base",
                    description=(
                        "Retrieve up to "
                        f"{request.max_results} relevant chunks from assets already "
                        "processed into this project's knowledge base."
                    ),
                )
            )
        if request.include_web:
            steps.append(
                PlanStep(
                    index=len(steps),
                    node=ResearchNode.WEB_RESEARCH,
                    title="Gather external references",
                    description=(
                        "Only if the project's own evidence is insufficient, "
                        f"retrieve up to {request.max_results} external web "
                        "references to fill the gap."
                    ),
                )
            )

        steps.append(
            PlanStep(
                index=len(steps),
                node=ResearchNode.CONTEXT_BUILDER,
                title="Build the working context",
                description=(
                    "Rank, de-duplicate, and merge all retrieved evidence into a "
                    "single context the synthesis step can be held to."
                ),
            )
        )
        steps.append(
            PlanStep(
                index=len(steps),
                node=ResearchNode.SYNTHESIS,
                title="Synthesize the deliverable",
                description=f"Produce {deliverable}, citing every piece of evidence used.",
            )
        )

        return ResearchPlan(
            strategy=self.name,
            intent=intent,
            objective=self._objective(request.query, deliverable),
            rationale=self._rationale(request, intent),
            keywords=keywords,
            steps=steps,
        )

    @staticmethod
    def _classify(query: str) -> tuple[str, str]:
        """Map the request onto a known intent and its deliverable shape."""
        lowered = query.lower()
        for intent, signals, deliverable in _INTENT_SIGNALS:
            if any(signal in lowered for signal in signals):
                return intent, deliverable
        return _DEFAULT_INTENT, _DEFAULT_DELIVERABLE

    @staticmethod
    def _objective(query: str, deliverable: str) -> str:
        """Restate the request as the single goal the run is judged against."""
        normalized = " ".join(query.split()).rstrip("?.! ")
        return f"Produce {deliverable} for: {normalized}"

    @staticmethod
    def _rationale(request: PlanRequest, intent: str) -> str:
        """Explain, in plain language, why the plan looks the way it does."""
        if request.include_assets and request.include_web:
            sourcing = (
                "Both retrieval sources are enabled, so the project knowledge base is "
                "searched first and external references are gathered to supplement it."
            )
        elif request.include_assets:
            sourcing = (
                "Only the project knowledge base is enabled, so the answer will be "
                "grounded exclusively in the project's own assets."
            )
        elif request.include_web:
            sourcing = (
                "Only external research is enabled, so the project's own assets are "
                "not consulted for this run."
            )
        else:
            sourcing = (
                "No retrieval source is enabled, so synthesis will report that the "
                "request cannot be grounded in evidence."
            )
        return (
            f"Request classified as '{intent}'. {sourcing} Context building and "
            "synthesis always run last so the final answer is derived from one "
            "merged, citable evidence set rather than from each source in isolation."
        )


def get_planner() -> PlannerStrategy:
    """Return the planner strategy for this deployment.

    The single place to swap in an LLM-backed planner. Kept as a
    function (not a module-level constant) so it can be overridden per
    request or per test through the graph's dependency container.
    """
    return RuleBasedPlanner()
