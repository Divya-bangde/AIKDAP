import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  POLL_TIMEOUT_MS,
  ReportRunPanel,
  isReportTerminal,
} from "@/features/reports/ReportRunPanel";
import { usePolling } from "@/hooks/usePolling";
import { messageFor } from "@/lib/api-error";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type ReportKind = components["schemas"]["ReportKind"];

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
    generateMutation.reset();
    retryMutation.reset();
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

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

        {generateMutation.isError && (
          <p role="alert" className="text-sm text-destructive">
            {messageFor(generateMutation.error)}
          </p>
        )}

        <ReportRunPanel
          report={reportQuery.data}
          started={assetId !== null}
          timedOut={reportQuery.timedOut}
          onRetry={(id) => retryMutation.mutate(id)}
          retrying={retryMutation.isPending}
          retryError={retryMutation.isError ? retryMutation.error : null}
        />

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
