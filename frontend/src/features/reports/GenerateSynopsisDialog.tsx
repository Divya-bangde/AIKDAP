import { useMutation } from "@tanstack/react-query";
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
import { usePolling } from "@/hooks/usePolling";
import * as assetsService from "@/services/assets";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];
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

function isReportTerminal(asset: AssetRead): boolean {
  return asset.processing_status === "completed" || asset.processing_status === "failed";
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

  const generateMutation = useMutation({
    mutationFn: (selected: ReportKind) => reportsService.generateSynopsis(projectId, selected),
    onSuccess: (accepted) => setAssetId(accepted.asset_id),
  });

  const reportQuery = usePolling({
    queryKey: ["reports", "asset", assetId],
    queryFn: () => assetsService.getAsset(assetId as string),
    isTerminal: isReportTerminal,
    enabled: assetId !== null,
    timeoutMs: POLL_TIMEOUT_MS,
  });

  function reset() {
    setKind(null);
    setAssetId(null);
    generateMutation.reset();
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  const asset = reportQuery.data;
  const failed = asset?.processing_status === "failed";
  const completed = asset?.processing_status === "completed";
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
            <StatusBadge domain="assetProcessing" value={asset?.processing_status ?? "pending"} />
            <p className="text-sm text-muted-foreground">Generating your report…</p>
          </div>
        )}

        {reportQuery.timedOut && (
          <p role="alert" className="text-sm text-destructive">
            This is taking longer than expected. Check back later in Assets.
          </p>
        )}

        {failed && (
          <p role="alert" className="text-sm text-destructive">
            {asset?.processing_error ?? "Report generation failed."}
          </p>
        )}

        {completed && asset && (
          <div className="flex gap-2" role="status">
            <Button
              variant="outline"
              onClick={() => reportsService.downloadReport(asset.id, "docx", `${asset.title}.docx`)}
            >
              Download DOCX
            </Button>
            <Button
              variant="outline"
              onClick={() => reportsService.downloadReport(asset.id, "pdf", `${asset.title}.pdf`)}
            >
              Download PDF
            </Button>
          </div>
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
