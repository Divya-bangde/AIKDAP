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
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];

/** The papers a build plan can actually be built from: this project's
 * own uploaded documents that finished processing. Mirrors the
 * backend's `ReportRepository.list_processed_documents` filter, so the
 * picker never offers something the endpoint would reject with a 422. */
function buildablePapers(assets: AssetRead[]): AssetRead[] {
  return assets.filter(
    (asset) => asset.asset_type === "document" && asset.processing_status === "completed",
  );
}

export function BuildPlanDialog({
  open,
  onOpenChange,
  projectId,
  assets,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  assets: AssetRead[];
}) {
  const papers = buildablePapers(assets);
  // All ticked by default (spec'd behaviour): the common case is
  // "build from everything I uploaded", and unticking is cheaper than
  // ticking one by one.
  const [deselected, setDeselected] = useState<Set<string>>(new Set());
  const [assetId, setAssetId] = useState<string | null>(null);

  const selectedIds = papers.map((paper) => paper.id).filter((id) => !deselected.has(id));

  const generateMutation = useMutation({
    mutationFn: (ids: string[]) => reportsService.generateBuildPlan(projectId, ids),
    onSuccess: (accepted) => setAssetId(accepted.asset_id),
  });

  const queryClient = useQueryClient();
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

  function toggle(id: string) {
    setDeselected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      setDeselected(new Set());
      setAssetId(null);
      generateMutation.reset();
      retryMutation.reset();
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Get build plan</DialogTitle>
          <DialogDescription>
            Pick the papers you want to turn into a real project. AIKDAP reads what has to be
            built, researches further, and recommends tools and a step-by-step process.
          </DialogDescription>
        </DialogHeader>

        {!assetId && (
          <div className="flex max-h-64 flex-col gap-2 overflow-y-auto">
            {papers.length === 0 && (
              <p className="text-sm text-muted-foreground">
                This project has no processed documents yet. Upload a paper and wait for
                processing to finish.
              </p>
            )}
            {papers.map((paper) => (
              <label
                key={paper.id}
                className="flex cursor-pointer items-center gap-3 rounded-md border border-border p-3"
              >
                <input
                  type="checkbox"
                  checked={!deselected.has(paper.id)}
                  onChange={() => toggle(paper.id)}
                  className="h-4 w-4"
                />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{paper.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {paper.file_name}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}

        {generateMutation.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not start the build plan. Check your selection and try again.
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
              // At least one paper is required; the endpoint enforces
              // it too, but the button should never send a request
              // that is guaranteed to 422.
              disabled={selectedIds.length === 0 || generateMutation.isPending}
              onClick={() => generateMutation.mutate(selectedIds)}
            >
              {generateMutation.isPending ? "Starting…" : "Get build plan"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
