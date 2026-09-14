"""Shared state contract for the research LangGraph workflow.

`ResearchState` is the single channel every node reads from and writes
to. Per the engineering constitution, agents never call each other
directly — a node's only output is a partial state update, and the
graph decides who runs next. Everything here is JSON-serializable so
the same structures can be persisted verbatim into the `research_runs`,
`research_steps`, and `agent_messages` tables without a translation
layer.

Two channels use `operator.add` reducers (`documents`, `messages`):
each node *appends* to them rather than replacing them, so retrieval
results and the agent transcript accumulate across the run. Every other
channel is last-write-wins, which is what the sequential planner ->
router -> ... -> synthesis flow expects.
"""

import enum
import operator
from typing import Annotated, Any, TypedDict


class ResearchNode(str, enum.Enum):
    """Canonical node names for the research graph.

    Owned here rather than in `app.modules.research` because the graph
    defines its own topology; the research module persists these values
    as plain strings (`research_steps.node_name`) so adding a node in a
    future sprint never requires a database migration.
    """

    PLANNER = "planner"
    ROUTER = "router"
    ASSET_RETRIEVAL = "asset_retrieval"
    WEB_RESEARCH = "web_research"
    CONTEXT_BUILDER = "context_builder"
    SYNTHESIS = "synthesis"
    #: Milestone 10 step 2: suggests OpenAlex papers for a gap in the
    #: final answer. Not in `RETRIEVAL_NODES` -- the router never
    #: dispatches to it, and it contributes no evidence to `context`.
    PAPER_SUGGESTION = "paper_suggestion"


#: Nodes the router may dispatch to. Used by the planner to build the
#: plan and by the router to derive the execution route.
RETRIEVAL_NODES: tuple[ResearchNode, ...] = (
    ResearchNode.ASSET_RETRIEVAL,
    ResearchNode.WEB_RESEARCH,
)


#: Suffix `tracking.instrument` uses for the `intermediate_results` key
#: recording a non-critical agent failure.
FAILURE_KEY_SUFFIX = "_failure"


def degraded_warnings(state: "ResearchState") -> list[str]:
    """Human-readable notices for every non-critical failure so far.

    Read by the synthesis node so the deliverable can state plainly
    that a source was unavailable, rather than silently presenting
    partial evidence as if every source had been consulted.

    Lives here, with the state it reads, rather than beside the
    instrumentation that writes it — `tracking` imports the registry,
    which imports the nodes, so a node importing `tracking` would close
    an import cycle.
    """
    warnings: list[str] = []
    for key, value in sorted((state.get("intermediate_results") or {}).items()):
        if not key.endswith(FAILURE_KEY_SUFFIX) or not isinstance(value, dict):
            continue
        warnings.append(
            f"The '{value.get('node', key)}' agent failed "
            f"({value.get('error_type', 'error')}: "
            f"{value.get('error_message', 'unknown error')}) and contributed no "
            "evidence to this run."
        )
    return warnings


class PlannedStep(TypedDict):
    """One entry in the planner's execution plan."""

    index: int
    node: str
    title: str
    description: str


class EvidenceProvenance(TypedDict, total=False):
    """Optional identity a retriever attaches to the evidence it returns.

    Split out and `total=False` so both `RetrievedDocument` and
    `Citation` carry the same optional identity fields, and so a
    retriever that cannot supply them (the simulated web provider has
    no chunk) simply omits them rather than filling in nulls.

    These are what make an answer *traceable*: `chunk_id` and
    `asset_id` let a stored citation be resolved back to the exact row
    the evidence came from, and the two scores record how it was
    selected. `retrieval_score` and `rerank_score` are measurements on
    different scales from different models and neither is derived from
    the other — the reranker's score is absent, not zero, when stage 2
    did not run.
    """

    #: The `knowledge_chunks` row this evidence is the text of.
    chunk_id: str
    #: The asset that chunk was extracted from.
    asset_id: str
    #: The asset's stored file name, for display alongside `title`.
    file_name: str
    #: Position within this retriever's own final ordering (1-based).
    #: The context builder merges sources by this, so a retriever's
    #: ranking survives the merge instead of being re-derived.
    rank: int
    #: Position before reranking (1-based); equals `rank` when stage 2
    #: did not run.
    retrieval_rank: int
    #: Stage-1 similarity (`1 - cosine distance`).
    retrieval_score: float
    #: Stage-2 cross-encoder score, when reranking ran.
    rerank_score: float
    #: The `RerankingStatus` value for the search that produced this
    #: item, so a stored citation records whether it was reranked.
    reranking_status: str
    #: The relevance-gate threshold this item's `rerank_score` cleared.
    #: Absent when no gate ran, which is not the same as passing one.
    relevance_threshold: float
    #: Sprint 12.1: the source location within the asset this evidence
    #: came from, when the extractor recorded one — a PDF page number,
    #: an XLSX sheet name, a DOCX/HTML heading or PPTX slide title.
    #: Absent (not null) for chunks with no such locator, same
    #: `total=False` convention as every other key here.
    page_number: int
    sheet_name: str
    section: str


#: The optional identity keys, derived from the TypedDict rather than
#: restated, so adding a provenance field cannot be forgotten in the
#: code that copies evidence identity from a document to its citation.
PROVENANCE_KEYS: tuple[str, ...] = tuple(EvidenceProvenance.__annotations__)


class RetrievedDocument(EvidenceProvenance):
    """A single retrieved evidence item, from any source.

    `reference` is the citation key carried through to the synthesized
    answer, so it must be stable and uniquely identify the origin
    (e.g. `asset:<uuid>#chunk-3`, `mock://web-research/...`).

    `provider` and `simulated` are carried explicitly rather than being
    re-derived downstream from `source`: a retriever knows whether its
    own output is real, and every consumer needs that fact to build an
    honest citation. Inferring it from the source string is what caused
    web evidence to be silently dropped from citations.
    """

    source: str
    provider: str
    reference: str
    title: str
    snippet: str
    score: float
    simulated: bool


class Citation(EvidenceProvenance):
    """A structured citation for one piece of evidence used in the answer.

    Structured rather than a bare reference string so a client can
    render a real citation (title, link, provenance) without parsing
    the answer text, and so `simulated` survives into
    `research_runs.citations` — a caller must always be able to tell
    placeholder evidence from genuine evidence.

    `id` is the short inline key (`c1`, `c2`, ...) used in the answer
    body. It is assigned from the run's ranked, de-duplicated evidence
    order, which is deterministic, so the same run re-executed produces
    the same keys. It is also the only identifier the synthesis model
    is permitted to cite by, and every id it returns is checked against
    the set actually supplied to it.

    Inherits `EvidenceProvenance`, so a citation built from a knowledge
    chunk carries the chunk and asset it came from while one built from
    a source with no such identity simply omits those keys.
    """

    id: str
    reference: str
    title: str
    source: str
    provider: str
    snippet: str
    score: float
    simulated: bool


class AgentMessagePayload(TypedDict):
    """A transcript entry emitted by a node.

    Persisted as one `agent_messages` row. This is the Explainable-AI
    record of *what each agent said and why*, distinct from the
    structural step record below.
    """

    role: str
    agent_name: str
    content: str
    metadata: dict[str, Any]


class StepRecord(TypedDict):
    """A node's structural report of its own execution.

    Persisted as one `research_steps` row. Overwritten by each node
    rather than accumulated: the execution service reads it from that
    node's individual stream update, so no history is lost.
    """

    node: str
    title: str
    summary: str
    output: dict[str, Any]


class ResearchState(TypedDict, total=False):
    """The graph's single shared state channel.

    This is the one canonical state for the research workflow — there
    is deliberately no second, competing state model. Every node reads
    and writes slices of it, and the orchestrator propagates it.

    `total=False` because nodes contribute their own slices; only the
    input fields are guaranteed present when the graph starts.

    Three channels carry reducers so parallel or repeated writes merge
    instead of clobbering:
    `retrieved_documents` and `messages` append (`operator.add`), and
    `intermediate_results` merges key-wise (`operator.or_`). Everything
    else is last-write-wins, which is what the sequential
    planner -> router -> ... -> synthesis flow expects.
    """

    # --- Inputs, set by the caller before the graph starts ---
    run_id: str
    project_id: str
    owner_id: str
    # The task this run belongs to, when it was started from one.
    # Optional: a run can be started directly against a project.
    task_id: str | None
    query: str
    
    # Workspace Context (from UI) for query resolution.
    workspace_context: dict[str, Any] | None
    include_assets: bool
    include_web: bool
    max_results: int
    # The completed run this one follows up on, when it is a follow-up:
    # `{"query", "answer", "asset_id"}`. Conversation context for
    # retrieval and synthesis only -- never evidence, never cited.
    parent: dict[str, Any] | None

    # --- Planner node output ---
    plan: dict[str, Any]
    objective: str

    # --- Router node output: the agents this run will actually execute ---
    selected_agents: list[str]

    # --- Retrieval node output (accumulated across every source) ---
    retrieved_documents: Annotated[list[RetrievedDocument], operator.add]

    # --- Context builder output ---
    context: str

    # --- Citations, built by the context builder and confirmed by
    # --- synthesis (last write wins, so synthesis is authoritative).
    citations: list[Citation]

    # --- Synthesis node output ---
    final_answer: str
    # How well the answer is supported by the evidence that reached the
    # synthesizer — a `ResearchGroundingStatus` value. Separate from
    # `status` below: a run can complete successfully and still report
    # that the evidence was insufficient to answer.
    grounding_status: str
    # A validated chart/diagram spec, only when the question asked for
    # one (see `schemas.Visualization`).
    visualization: dict[str, Any] | None
    # Validated equations with backend-sampled curves, when the question
    # involved any (see `planner.equations`).
    equations: list[dict[str, Any]]

    # --- Web fallback control: external search runs only when the
    # --- project's own evidence was insufficient, and at most once.
    web_research_attempted: bool
    web_fallback: bool
    # How the question relates to the topic of the project's documents (a
    # `schemas.TopicRelation` value). Decided on the first synthesis pass,
    # against the project's own evidence, and kept through the fallback.
    topic_relation: str

    # --- Paper suggestion node output (Milestone 10 step 2): papers
    # --- OpenAlex suggested to help close a gap in the answer. Never
    # --- read by synthesis -- this node runs strictly after it.
    suggested_papers: list[dict[str, Any]]
    # Whether a configured OpenAlex provider makes `paper_suggestion`
    # worth entering at all. Set once, in `synthesis_node`, so
    # `route_after_synthesis` stays a pure function of state -- the
    # same pattern `web_fallback` already uses.
    paper_suggestion_eligible: bool

    # --- Per-node side output that is not part of the main flow:
    # --- degraded-mode notices, non-critical node failures, and
    # --- anything a future agent needs to hand to a later one without
    # --- widening this contract for every new capability.
    intermediate_results: Annotated[dict[str, Any], operator.or_]

    # --- Run-level outcome, mirrored from `ResearchRunStatus` values.
    # --- The database row remains authoritative; this exists so a node
    # --- can see the run's own state without a database read.
    status: str
    # Set only when the run has failed critically.
    error: str | None

    # --- Cross-cutting observability ---
    messages: Annotated[list[AgentMessagePayload], operator.add]
    step: StepRecord
