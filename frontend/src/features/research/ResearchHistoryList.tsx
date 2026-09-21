import { AnimatePresence, motion } from "motion/react";
import { FileText, Search, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/common/EmptyState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Stagger, StaggerItem } from "@/components/motion/PageTransition";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { formatDuration, formatRelativeTime } from "@/lib/format";
import { hoverLift, layoutSpring } from "@/lib/motion";
import { availableStatuses, filterRuns } from "@/features/research/research-history";
import { outcomeLabel, runOutcome } from "@/features/research/research-presentation";
import type { components } from "@/types/api";

type ResearchRunRead = components["schemas"]["ResearchRunRead"];

/** Stable across a history row and the run view's own heading, so
 * Motion animates one element between the two routes instead of
 * cross-fading two unrelated ones — the same device the project card
 * already uses to become a workspace header, applied to the other
 * place in the product where a list item expands into a page
 * (Sprint 9K.6).
 *
 * Derived from the run id, so two rows can never share an id and the
 * wrong pair can never be matched.
 *
 * Deliberately used in exactly one list. The dashboard shows a running
 * run in *two* places at once — Active Work and Recent Research Runs —
 * and a `layoutId` mounted twice makes Motion pick a winner
 * arbitrarily, so the dashboard's rows stay plain and the project
 * workspace owns this transition. */
export function researchLayoutId(runId: string): string {
  return `research-query-${runId}`;
}

/** A project's research history: every run it has ever produced,
 * filterable by the same status a run's own badge shows and searched
 * by question text — entirely client-side over the list the caller
 * already fetched (Sprint 9K.5, section 7). `GET /research/runs` takes
 * no search or status parameter; adding one would be exactly the
 * cosmetic backend change this sprint rules out, and a project's
 * research history is small enough that filtering in the browser costs
 * nothing a real user would notice. */
export function ResearchHistoryList({
  runs,
  initialStatus = null,
}: {
  runs: ResearchRunRead[];
  initialStatus?: string | null;
}) {
  const [status, setStatus] = useState<string | null>(initialStatus);
  const [search, setSearch] = useState("");

  const statuses = availableStatuses(runs);
  const filtered = filterRuns(runs, { status, search });
  const isFiltered = status !== null || search.trim() !== "";

  return (
    <div className="flex flex-col gap-3">
      {(statuses.length > 1 || runs.length > 3) && (
        <div className="flex flex-wrap items-center gap-2 print:hidden">
          <div className="relative flex-1 sm:max-w-xs">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search questions"
              aria-label="Search research history"
              className="h-8 w-full rounded-md border border-input bg-background pl-8 pr-3 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            />
          </div>

          {statuses.length > 1 && (
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by status">
              <StatusChip label="All" active={status === null} onClick={() => setStatus(null)} />
              {statuses.map((value) => (
                <StatusChip
                  key={value}
                  label={outcomeLabel(value)}
                  active={status === value}
                  onClick={() => setStatus(value)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {filtered.length === 0 ? (
        <EmptyState
          icon={isFiltered ? Search : FileText}
          title={isFiltered ? "No research matches this filter." : "No research runs yet."}
          description={
            isFiltered
              ? "Try a different question or clear the status filter."
              : "Ask a question to produce a grounded, cited answer."
          }
          action={
            isFiltered ? (
              <button
                type="button"
                onClick={() => {
                  setStatus(null);
                  setSearch("");
                }}
                className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
              >
                <X className="h-3 w-3" />
                Clear filters
              </button>
            ) : undefined
          }
        />
      ) : (
        <Card>
          <CardContent className="flex flex-col gap-1 p-3">
            <Stagger className="flex flex-col gap-1">
              {/* Sprint 9K.9: a run leaving the filtered view fades out
               * rather than vanishing mid-list — the same "filtering
               * list transitions" the sprint asks for, reusing
               * `staggerItem`'s own `exit` clause rather than a new
               * variant. */}
              <AnimatePresence initial={false}>
                {filtered.map((run) => {
                  const outcome = runOutcome(run);
                  const citationCount = run.citations?.length ?? 0;
                  return (
                    <StaggerItem key={run.id}>
                      <motion.div {...hoverLift}>
                        <Link
                          to={`/research/${run.id}`}
                          className="flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-sunken"
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="min-w-0">
                              <motion.span
                                layoutId={researchLayoutId(run.id)}
                                transition={layoutSpring}
                                className="line-clamp-1 block text-sm font-medium"
                              >
                                {run.query}
                              </motion.span>
                              <span className="text-xs text-muted-foreground">
                                {formatRelativeTime(run.created_at)}
                                {run.duration_ms !== null && ` · ${formatDuration(run.duration_ms)}`}
                                {citationCount > 0 &&
                                  ` · ${citationCount} citation${citationCount === 1 ? "" : "s"}`}
                              </span>
                            </span>
                          </div>
                          <StatusBadge domain={outcome.domain} value={outcome.value} />
                        </Link>
                      </motion.div>
                    </StaggerItem>
                  );
                })}
              </AnimatePresence>
            </Stagger>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function StatusChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-background text-muted-foreground hover:border-border-strong hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}
