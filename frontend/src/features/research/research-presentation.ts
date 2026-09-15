import { hasStatus, statusBadge } from "@/lib/status";

/** Plain-language labels for the pipeline's real stages (Sprint 9K.8).
 *
 * The backend's own step titles are accurate but written for someone
 * who already knows the architecture ("Route to retrieval agents",
 * "Build the working context"). An executive watching a run doesn't
 * need to know an agent was routed — they need to know AIKDAP is
 * figuring out where to look.
 *
 * This maps the six real `node_name` values a run actually produces —
 * confirmed against `backend/app/agents/planner/nodes.py`, not
 * guessed — to static, plain-language copy. It is deliberately static:
 * these describe what a stage of this *kind* always does, not a
 * measurement of this run. That is why they read like the landing
 * page's own explanatory copy rather than like `step.summary` (which
 * IS a real, run-specific measurement and remains available, verbatim,
 * in the technical detail view).
 *
 * Nothing here invents a stage the backend doesn't have. There is no
 * seventh "Checking Grounding" entry: grounding verification is real,
 * but it happens *inside* synthesis (every citation the model returns
 * is checked against the evidence actually supplied to it) rather than
 * as its own backend step — so that fact is folded into synthesis's
 * own description instead of being presented as a stage with its own
 * status and timing it does not really have. */
export interface StepPresentation {
  title: string;
  description: string;
}

const PRESENTATION: Record<string, StepPresentation> = {
  planner: {
    title: "Understanding your question",
    description: "Working out what you're asking and how to answer it.",
  },
  router: {
    title: "Choosing where to search",
    description: "Deciding which sources are relevant to this question.",
  },
  asset_retrieval: {
    title: "Searching your knowledge base",
    description: "Finding relevant information from your uploaded documents.",
  },
  web_research: {
    title: "Searching external references",
    description: "Gathering supporting references from outside your documents.",
  },
  context_builder: {
    title: "Evaluating the evidence",
    description: "Selecting the strongest passages to support an answer.",
  },
  synthesis: {
    title: "Generating the answer",
    description: "Writing a response from verified evidence — every citation is checked against what was actually supplied.",
  },

  // Report runs (Milestone 10 step 4) share this pipeline view; these
  // are the four real `node_name`s from `backend/app/agents/reports`.
  collect_documents: {
    title: "Gathering your documents",
    description: "Collecting every processed document in this project.",
  },
  retrieve_evidence: {
    title: "Finding evidence for each section",
    description: "Searching your documents for passages that support each section.",
  },
  write_sections: {
    title: "Writing the report",
    description: "Drafting each section only from that evidence — a section without evidence is marked as not covered.",
  },
  coverage_check: {
    title: "Checking coverage",
    description: "Building the references from the documents actually cited.",
  },
};

/** Falls back to the raw `node_name` rather than a blank or invented
 * label — a stage type this frontend doesn't yet recognise is still
 * honestly named, never hidden or guessed at. */
export function presentationFor(nodeName: string): StepPresentation {
  return PRESENTATION[nodeName] ?? { title: nodeName, description: "" };
}

/** Sprint 9K.9: the single source of truth for "what outcome should a
 * run's badge show", used by every surface that lists a run — Dashboard,
 * `ResearchHistoryList`, `ResearchRunView`. Before this sprint the same
 * `run.grounding_status ? "grounding" : "researchRun"` /
 * `run.grounding_status ?? run.status` pair was written out inline in
 * all three places (plus once more inside `research-history.ts`'s own
 * `displayStatus`), which is exactly the kind of duplicate presentation
 * logic that lets one surface drift from the others. There is now one
 * function that decides it. */
export interface RunOutcome {
  domain: "grounding" | "researchRun";
  value: string;
}

export function runOutcome(run: { status: string; grounding_status: string | null }): RunOutcome {
  return run.grounding_status
    ? { domain: "grounding", value: run.grounding_status }
    : { domain: "researchRun", value: run.status };
}

/** The label `runOutcome`'s value would render as a badge, without
 * needing a full run object — for places (like a filter chip) that only
 * have the raw status string. Tries the grounding vocabulary first,
 * then the run vocabulary; the two value sets don't collide (both map
 * their own "failed" to the same "Research failed" copy — see
 * `lib/status.ts`), so trying grounding first is safe rather than
 * arbitrary. */
export function outcomeLabel(value: string): string {
  if (hasStatus("grounding", value)) return statusBadge("grounding", value).label;
  return statusBadge("researchRun", value).label;
}

/** Whether a run counts as a grounded answer for the "Grounded Answers"
 * KPI (Sprint 9K.10, completing 9K.9's own recommendation).
 *
 * The rule was already correct in both places that used it — Command
 * Center's KPI tile and the per-project count on a project card — but
 * it was written out twice as a bare `grounding_status === "grounded"`,
 * the last remaining pair of hand-rolled research-outcome semantics in
 * the frontend. Behaviour is deliberately unchanged; this only gives
 * the rule one home alongside `runOutcome`.
 *
 * Two things it deliberately does NOT do:
 *
 * 1. It never counts a run because `status === "completed"`. A
 *    completed run that could not ground an answer is not a grounded
 *    answer, which is the distinction this whole product exists to
 *    make.
 * 2. It excludes `partially_grounded`. That state means the model
 *    returned at least one valid citation *and* some citation ids that
 *    were never supplied to it (those were rejected). Counting it here
 *    would inflate a headline number the tile captions "Backed by
 *    cited evidence" — the conservative reading is the honest one. */
export function isGroundedAnswer(run: { grounding_status: string | null }): boolean {
  return run.grounding_status === "grounded";
}
