import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/common/StatusBadge";
import { ResearchPipeline } from "@/features/research/ResearchPipeline";
import { messageFor } from "@/lib/api-error";
import * as reportsService from "@/services/reports";
import { useState } from "react";
import type { components } from "@/types/api";

type ReportRead = components["schemas"]["ReportRead"];
type ReportStep = NonNullable<ReportRead["steps"]>[number];

// ponytail: fixed 6-minute cap, not configurable per-report -- mirrors
// `ResearchRunView.POLL_TIMEOUT_MS`'s reasoning for a hard safety net,
// scaled down since a report is a handful of LLM calls, not a full
// research run.
export const POLL_TIMEOUT_MS = 6 * 60 * 1000;

export function isReportTerminal(report: ReportRead): boolean {
  return report.processing_status === "completed" || report.processing_status === "failed";
}

/** The report's steps grouped by generation attempt, oldest first --
 * each attempt is its own pipeline run (1, then +1 per retry). */
export function stepsByAttempt(steps: ReportStep[]): [number, ReportStep[]][] {
  const groups = new Map<number, ReportStep[]>();
  for (const step of steps) {
    groups.set(step.attempt, [...(groups.get(step.attempt) ?? []), step]);
  }
  return [...groups.entries()].sort(([a], [b]) => a - b);
}

/** Everything a report run looks like once it has started: progress,
 * the attempt-grouped pipeline, the timeout notice, failure + retry,
 * and the two download buttons.
 *
 * Extracted from `GenerateSynopsisDialog` rather than reimplemented, so
 * the synopsis and the build plan cannot drift into two different
 * progress UIs (design ruling R7). The dialogs keep only what differs:
 * their own picker and their own generate mutation. */
export function ReportRunPanel({
  report,
  started,
  timedOut,
  onRetry,
  retrying,
  retryError,
}: {
  report: ReportRead | undefined;
  /** A generation run has been started; the first poll may not have
   * returned yet. Distinct from `report !== undefined` so the progress
   * badge stays visible during that gap instead of the panel going
   * blank. */
  started: boolean;
  timedOut: boolean;
  onRetry: (assetId: string) => void;
  retrying: boolean;
  retryError: unknown;
}) {
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const failed = report?.processing_status === "failed";
  const completed = report?.processing_status === "completed";
  const inProgress = started && !completed && !failed && !timedOut;

  function handleDownload(id: string, format: "docx" | "pdf", filename: string) {
    setDownloadError(null);
    reportsService.downloadReport(id, format, filename).catch((error: unknown) => {
      setDownloadError(messageFor(error));
    });
  }

  return (
    <>
      {inProgress && (
        <div className="flex items-center gap-2" role="status">
          <StatusBadge domain="assetProcessing" value={report?.processing_status ?? "pending"} />
          <p className="text-sm text-muted-foreground">Generating your report…</p>
        </div>
      )}

      {report && report.steps && report.steps.length > 0 && (
        <div className="flex max-h-72 flex-col gap-4 overflow-y-auto rounded-lg bg-sunken p-4">
          {stepsByAttempt(report.steps).map(([attempt, steps], index, groups) => (
            <section key={attempt} aria-label={`Attempt ${attempt}`}>
              {/* Headings only once a report has been retried: a
               * single run needs no "Attempt 1" label. */}
              {groups.length > 1 && (
                <p className="mb-2 text-label uppercase text-muted-foreground">
                  Attempt {attempt}
                  {index === groups.length - 1 ? " (latest)" : ""}
                </p>
              )}
              <ResearchPipeline steps={steps} />
            </section>
          ))}
        </div>
      )}

      {timedOut && (
        <p role="alert" className="text-sm text-destructive">
          This is taking longer than expected. Check back later in Assets.
        </p>
      )}

      {failed && (
        <div className="flex flex-col gap-2">
          <p role="alert" className="text-sm text-destructive">
            {report?.processing_error ?? "Report generation failed."}
          </p>
          {retryError !== null && retryError !== undefined && (
            <p role="alert" className="text-sm text-destructive">
              {messageFor(retryError)}
            </p>
          )}
          <div>
            <Button variant="outline" disabled={retrying} onClick={() => report && onRetry(report.id)}>
              {retrying ? "Retrying…" : "Retry"}
            </Button>
          </div>
        </div>
      )}

      {completed && report && (
        <div className="flex gap-2" role="status">
          <Button
            variant="outline"
            onClick={() => handleDownload(report.id, "docx", `${report.title}.docx`)}
          >
            Download DOCX
          </Button>
          <Button
            variant="outline"
            onClick={() => handleDownload(report.id, "pdf", `${report.title}.pdf`)}
          >
            Download PDF
          </Button>
        </div>
      )}

      {downloadError && (
        <p role="alert" className="text-sm text-destructive">
          {downloadError}
        </p>
      )}
    </>
  );
}
