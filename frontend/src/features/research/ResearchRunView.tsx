import { motion } from "motion/react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { ErrorState } from "@/components/common/ErrorState";
import { ExportMenu } from "@/components/common/ExportMenu";
import { ResearchRunSkeleton } from "@/components/common/Skeletons";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { FollowUpPrompt } from "@/features/research/FollowUpPrompt";
import { ResearchPipeline } from "@/features/research/ResearchPipeline";
import { researchLayoutId } from "@/features/research/ResearchHistoryList";
import { ResearchResult } from "@/features/research/ResearchResult";
import { runOutcome } from "@/features/research/research-presentation";
import { usePolling } from "@/hooks/usePolling";
import { fileSlug, researchRunMarkdown } from "@/lib/export";
import { fadeUp, layoutSpring } from "@/lib/motion";
import * as researchService from "@/services/research";
import type { components } from "@/types/api";

type ResearchRunDetail = components["schemas"]["ResearchRunDetail"];

const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);
const IN_PROGRESS_IMPORT_STATUSES = new Set(["queued", "processing"]);

// Residual 4a: if `finalize_paper_import` throws instead of ever writing
// a terminal `import_status`, `isTerminal` below never returns true and
// the UI would poll forever with no feedback. This cap ends the wait
// after ~10 minutes so the user sees an error instead of an infinite
// spinner. ponytail: fixed 10-minute cap, not configurable per-job.
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

function isTerminal(run: ResearchRunDetail): boolean {
  if (!TERMINAL_RUN_STATUSES.has(run.status)) return false;
  // Milestone 10 step 3 (Add & re-run): importing a suggested paper
  // happens after this run is already `completed`, so `usePolling`
  // must keep polling through it -- otherwise a card frozen on
  // "Queued" would never update once the run itself stopped changing.
  const papers = run.suggested_papers ?? [];
  const hasPendingImport = papers.some((paper) =>
    IN_PROGRESS_IMPORT_STATUSES.has((paper as { import_status?: string }).import_status ?? ""),
  );
  if (hasPendingImport) return false;

  // Final review Fix 4: the import chord finishes writing every
  // paper's final `import_status` a moment BEFORE its separate
  // `finalize_paper_import` callback actually creates and links the
  // child run. Stopping here, the instant every paper looks final,
  // would let polling stop one tick too early -- `rerun_run_id` would
  // still be null, and "View re-run" would never appear without a
  // manual reload. Keep polling until either no paper was added (no
  // re-run is coming) or the link has actually shown up.
  const hasAddedPaper = papers.some(
    (paper) => (paper as { import_status?: string }).import_status === "added",
  );
  if (hasAddedPaper && run.rerun_run_id == null) return false;

  return true;
}

/** Polls `GET /research/runs/{id}` until the backend reports a terminal
 * status, then stops. The UI never claims "completed" ahead of the
 * backend — every render reflects exactly `run.status`. */
export function ResearchRunView({ runId }: { runId: string }) {
  const runQuery = usePolling({
    queryKey: ["research", "run", runId],
    queryFn: () => researchService.getResearchRun(runId),
    isTerminal,
    timeoutMs: POLL_TIMEOUT_MS,
  });

  if (runQuery.isLoading) {
    return <ResearchRunSkeleton />;
  }

  if (runQuery.isError) {
    return <ErrorState error={runQuery.error} title="Could not load this research run" />;
  }

  const run = runQuery.data;
  if (!run) return null;

  const isRunning = !isTerminal(run) && !runQuery.timedOut;
  const outcome = runOutcome(run);
  const stepCount = run.steps?.length ?? 0;

  return (
    <div className="flex flex-col gap-6">
      {run.parent_run_id && run.added_paper_count != null && (
        <p className="text-sm text-muted-foreground">
          Re-run with {run.added_paper_count} added paper{run.added_paper_count === 1 ? "" : "s"}
        </p>
      )}
      <motion.div initial="hidden" animate="visible" variants={fadeUp}>
        <Card className="overflow-hidden">
          <CardHeader className="gap-3">
            {/* role="status" (implicit aria-live="polite") announces only
             * when the badge's own text actually changes -- i.e. when the
             * backend reports a new run status -- not on every poll tick
             * that returns the same status, since aria-live fires on DOM
             * mutation, not on refetch. The per-step list below is
             * deliberately outside this region: announcing every step
             * transition would be noise a screen-reader user didn't ask
             * for. */}
            <div className="flex flex-wrap items-start justify-between gap-3" role="status">
              <div className="min-w-0">
                {/* The query is this page's subject, so it is the page's
                 * single `h1` — axe flagged the run view as having no
                 * level-one heading when this was an `h2`.
                 *
                 * It also carries the `layoutId` of the row that was
                 * clicked in the project's research history, so arriving
                 * here animates that question into the heading rather
                 * than swapping one page for another: the run reads as
                 * the history item expanded, which is what it is. */}
                <motion.h1
                  layoutId={researchLayoutId(run.id)}
                  transition={layoutSpring}
                  className="text-title"
                >
                  {run.query}
                </motion.h1>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {isRunning && <Loader2 className="h-3.5 w-3.5 animate-spin text-ai" />}
                {/* Once the backend has decided an outcome, that outcome —
                 * not the bare fact that the async job finished — is what
                 * belongs in the page's most prominent badge.
                 *
                 * `run.status === "completed"` only means the pipeline
                 * ran to the end; it says nothing about whether an answer
                 * came out of it. Showing it alone put an identical green
                 * "Completed" pill here for a fully grounded answer AND
                 * for a deliberate insufficient-evidence decline — the
                 * one outcome this product exists to make unmistakable
                 * (confirmed side-by-side in a real browser, Sprint
                 * 9K.7). `runOutcome` (Sprint 9K.9) is the one place this
                 * resolution now lives — Dashboard and
                 * `ResearchHistoryList` render the same outcome for the
                 * same run through the same function. */}
                <StatusBadge domain={outcome.domain} value={outcome.value} />
                {run.status === "completed" && (
                  <ExportMenu
                    filename={fileSlug(run.query)}
                    toMarkdown={() => researchRunMarkdown(run)}
                    toJson={() => run}
                  />
                )}
              </div>
            </div>
          </CardHeader>

          <CardContent>
            {/* Once the run completes, the answer below is the page's
             * subject and the step trace is supporting detail, so it
             * collapses behind a native disclosure. It stays expanded
             * while running (it is the progress indicator) and on
             * failure (the failed step's error is the explanation). */}
            {run.status === "completed" ? (
              <details>
                <summary className="cursor-pointer text-label uppercase text-muted-foreground hover:text-foreground">
                  AI research pipeline · {stepCount} {stepCount === 1 ? "step" : "steps"}
                </summary>
                <div className="mt-4">
                  <ResearchPipeline steps={run.steps ?? []} />
                </div>
              </details>
            ) : (
              <>
                <p className="mb-4 text-label uppercase text-muted-foreground">
                  AI research pipeline
                </p>
                <ResearchPipeline steps={run.steps ?? []} />
              </>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {runQuery.timedOut && !isTerminal(run) && (
        <motion.div initial="hidden" animate="visible" variants={fadeUp}>
          <Card role="alert" className="overflow-hidden">
            <CardContent className="flex items-start gap-3 p-6">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-destructive/10 text-destructive">
                <AlertTriangle className="h-4.5 w-4.5" />
              </div>
              <div className="min-w-0">
                <p className="text-section">Import is taking longer than expected</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Reload the page to check for an updated status.
                </p>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {run.status === "failed" && (
        <motion.div initial="hidden" animate="visible" variants={fadeUp}>
          {/* The failure experience: states plainly that the pipeline
           * could not complete, and shows the backend's own already
           * scrubbed message. `LLMError` runs `scrub_secrets` in its
           * constructor, so no credential can reach this text, and the
           * frontend adds no stack trace of its own. */}
          <Card role="alert" className="overflow-hidden">
            <CardContent className="flex items-start gap-3 p-6">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-destructive/10 text-destructive">
                <AlertTriangle className="h-4.5 w-4.5" />
              </div>
              <div className="min-w-0">
                <p className="text-section">Research could not be completed</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  The intelligence pipeline could not finish synthesis. No answer was generated
                  and nothing was invented in its place.
                </p>
                {run.error_message && (
                  <p className="mt-3 rounded-lg bg-sunken p-3 font-mono text-xs leading-relaxed text-foreground">
                    {run.error_message}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {run.status === "completed" && (
        <ResearchResult run={run} followUp={<FollowUpPrompt runId={run.id} projectId={run.project_id} />} />
      )}
    </div>
  );
}
