import { runOutcome } from "@/features/research/research-presentation";
import type { ResearchRunSummary as ResearchRunRead } from "@/services/research";

/** The status a run's own badge actually shows — `grounding_status`
 * when the backend has one, `status` otherwise. Filtering must match
 * what filtering is *for*: letting someone find the row they can see,
 * not a second vocabulary they'd have to learn (Sprint 9K.5, section 7
 * — matches the precedence `ProjectDetail` and `Dashboard` already
 * render with). Sourced from `runOutcome`, the one place this
 * resolution is decided (Sprint 9K.9), rather than repeating it here. */
export function displayStatus(run: ResearchRunRead): string {
  return runOutcome(run).value;
}

/** The distinct statuses actually present in this list, in a fixed,
 * meaningful order — never a status that doesn't occur, so the filter
 * bar never offers a chip that would always be empty. */
const STATUS_ORDER = [
  "running",
  "pending",
  "grounded",
  "partially_grounded",
  "insufficient_evidence",
  "completed",
  "failed",
  "cancelled",
];

export function availableStatuses(runs: ResearchRunRead[]): string[] {
  const present = new Set(runs.map(displayStatus));
  const ordered = STATUS_ORDER.filter((status) => present.has(status));
  const rest = [...present].filter((status) => !STATUS_ORDER.includes(status)).sort();
  return [...ordered, ...rest];
}

/** Client-side only, over a list the caller has already fetched —
 * `GET /research/runs` has no filter or search parameters beyond
 * `project_id`, and this sprint's brief is explicit that cosmetic
 * filtering must not become a reason to add one. */
export function filterRuns(
  runs: ResearchRunRead[],
  { status, search }: { status: string | null; search: string },
): ResearchRunRead[] {
  const term = search.trim().toLowerCase();
  return runs.filter((run) => {
    if (status && displayStatus(run) !== status) return false;
    if (term && !run.query.toLowerCase().includes(term)) return false;
    return true;
  });
}
