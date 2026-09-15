import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/common/StatusBadge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ResearchPipeline } from "@/features/research/ResearchPipeline";
import { usePolling } from "@/hooks/usePolling";
import { messageFor } from "@/lib/api-error";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type ReportRead = components["schemas"]["ReportRead"];
type ReportKind = components["schemas"]["ReportKind"];

// ponytail: fixed 6-minute cap, not configurable per-report -- mirrors
// `ResearchRunView.POLL_TIMEOUT_MS`'s reasoning for a hard safety net,
// scaled down since a report is a handful of LLM calls, not a full
// research run.
const POLL_TIMEOUT_MS = 6 * 60 * 1000;

const KIND_OPTIONS: { value: ReportKind; label: string; description: string }[] = [
  {
    value: "study_summary",
    label: "Study summary",
    description: "Key themes, main findings per document, and how they relate.",
  },
  {
    value: "project_synopsis",
    label: "Project synopsis",
    description: "A formal write-up: abstract, methodology, literature review, and more.",
  },
];

function isReportTerminal(report: ReportRead): boolean {
  return report.processing_status === "completed" || report.processing_status === "failed";
}

type ReportStep = NonNullable<ReportRead["steps"]>[number];

/** The report's steps grouped by generation attempt, oldest first --
 * each attempt is its own pipeline run (1, then +1 per retry). */
function stepsByAttempt(steps: ReportStep[]): [number, ReportStep[]][] {
  const groups = new Map<number, ReportStep[]>();
  for (const step of steps) {
    groups.set(step.attempt, [...(groups.get(step.attempt) ?? []), step]);
  }
  return [...groups.entries()].sort(([a], [b]) => a - b);
}

export function GenerateSynopsisDialog({
  open,
  onOpenChange,
  projectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
}) {
  const [kind, setKind] = useState<ReportKind | null>(null);
  const [assetId, setAssetId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const generateMutation = useMutation({
    mutationFn: (selected: ReportKind) => reportsService.generateSynopsis(projectId, selected),
    onSuccess: (accepted) => setAssetId(accepted.asset_id),
  });

  const queryClient = useQueryClient();
  // ponytail: the 6-minute poll timeout keeps counting from the first
  // attempt; a retry after a timeout needs a reopened dialog. Reset
  // `usePolling`'s timer per attempt if that ever matters.
  const retryMutation = useMutation({
    mutationFn: (id: string) => reportsService.retryReport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", "report", assetId] }),
  });

  const reportQuery = usePolling({
    queryKey: ["reports", "report", assetId],
    queryFn: () => reportsService.getReport(assetId as string),
    isTerminal: isReportTerminal,
    enabled: assetId !== null,
    timeoutMs: POLL_TIMEOUT_MS,
  });

  function reset() {
    setKind(null);
    setAssetId(null);
    setDownloadError(null);
    generateMutation.reset();
    retryMutation.reset();
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  function handleDownload(id: string, format: "docx" | "pdf", filename: string) {
    setDownloadError(null);
    reportsService.downloadReport(id, format, filename).catch((error: unknown) => {
      setDownloadError(messageFor(error));
    });
  }

  const report = reportQuery.data;
  const failed = report?.processing_status === "failed";
  const completed = report?.processing_status === "completed";
  const inProgress = assetId !== null && !completed && !failed && !reportQuery.timedOut;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Generate synopsis</DialogTitle>
          <DialogDescription>
            Creates a report from this project&apos;s processed documents.
          </DialogDescription>
        </DialogHeader>

        {!assetId && (
          <div className="flex flex-col gap-3">
            {KIND_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setKind(option.value)}
                aria-pressed={kind === option.value}
                className={`rounded-md border p-3 text-left transition-colors ${
                  kind === option.value ? "border-primary bg-primary/5" : "border-border"
                }`}
              >
                <p className="font-medium">{option.label}</p>
                <p className="text-sm text-muted-foreground">{option.description}</p>
              </button>
            ))}
          </div>
        )}

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

        {reportQuery.timedOut && (
          <p role="alert" className="text-sm text-destructive">
            This is taking longer than expected. Check back later in Assets.
          </p>
        )}

        {failed && (
          <div className="flex flex-col gap-2">
            <p role="alert" className="text-sm text-destructive">
              {report?.processing_error ?? "Report generation failed."}
            </p>
            {retryMutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {messageFor(retryMutation.error)}
              </p>
            )}
            <div>
              <Button
                variant="outline"
                disabled={retryMutation.isPending}
                onClick={() => report && retryMutation.mutate(report.id)}
              >
                {retryMutation.isPending ? "Retrying…" : "Retry"}
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

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Close
          </Button>
          {!assetId && (
            <Button
              disabled={!kind || generateMutation.isPending}
              onClick={() => kind && generateMutation.mutate(kind)}
            >
              {generateMutation.isPending ? "Starting…" : "Generate"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
