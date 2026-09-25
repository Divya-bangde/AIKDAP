import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AnalysisCard } from "@/features/business-analytics/AnalysisCard";
import { DatasetProfile } from "@/features/business-analytics/DatasetProfile";
import { KaggleImportDialog } from "@/features/business-analytics/KaggleImportDialog";
import { messageFor } from "@/lib/api-error";
import * as analyticsService from "@/services/analytics";
import * as assetsService from "@/services/assets";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];

const SETTLED = new Set(["completed", "failed", "unsupported"]);

export function AnalyticsWorkspace({ projectId }: { projectId: string }) {
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [kaggleOpen, setKaggleOpen] = useState(false);

  const assetsQuery = useQuery({
    queryKey: ["assets", projectId],
    queryFn: () => assetsService.listAssets(projectId),
    refetchInterval: (query) => {
      const datasets = (query.state.data ?? []).filter((asset: AssetRead) => asset.asset_type === "dataset");
      return datasets.some((asset) => !SETTLED.has(asset.processing_status)) ? 3000 : false;
    },
  });

  const datasets = (assetsQuery.data ?? []).filter((asset) => asset.asset_type === "dataset");
  const completedDatasets = datasets.filter((asset) => asset.processing_status === "completed");
  const failedDatasets = datasets.filter((asset) => asset.processing_status === "failed");
  const selectedId = datasetId ?? completedDatasets[0]?.id ?? null;

  const queryClient = useQueryClient();
  const analysesQuery = useQuery({
    queryKey: ["analyses", selectedId],
    queryFn: () => analyticsService.listAnalyses(selectedId as string),
    enabled: Boolean(selectedId),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((a) => a.status === "pending" || a.status === "running") ? 2000 : false,
  });

  const askMutation = useMutation({
    mutationFn: (q: string) => analyticsService.createAnalysis(selectedId as string, q),
    onSuccess: () => {
      setQuestion("");
      queryClient.invalidateQueries({ queryKey: ["analyses", selectedId] });
    },
  });

  const analyses = analysesQuery.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      {datasets.length === 0 ? (
        <div className="rounded-card bg-card p-6 text-center shadow-subtle">
          <p className="text-sm text-muted-foreground">
            Upload a CSV or Excel file in Documents, or import one from Kaggle.
          </p>
          <Button className="mt-4" variant="outline" onClick={() => setKaggleOpen(true)}>
            Import from Kaggle
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm">
            <span className="font-medium">Dataset</span>
            <select
              className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              value={selectedId ?? ""}
              onChange={(event) => setDatasetId(event.target.value)}
              aria-label="Dataset"
            >
              {completedDatasets.length === 0 && <option value="">No dataset ready yet</option>}
              {completedDatasets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.title}
                </option>
              ))}
            </select>
          </label>
          <Button variant="outline" onClick={() => setKaggleOpen(true)}>
            Import from Kaggle
          </Button>
        </div>
      )}

      {failedDatasets.length > 0 && (
        <ul className="flex flex-col gap-1" aria-label="Failed datasets">
          {failedDatasets.map((asset) => (
            <li key={asset.id} role="alert" className="text-sm text-destructive">
              {asset.title} could not be loaded{asset.processing_error ? `: ${asset.processing_error}` : "."}
            </li>
          ))}
        </ul>
      )}

      <KaggleImportDialog
        open={kaggleOpen}
        onOpenChange={setKaggleOpen}
        projectId={projectId}
        onImported={() => queryClient.invalidateQueries({ queryKey: ["assets", projectId] })}
      />

      {selectedId && (
        <>
          <DatasetProfile datasetId={selectedId} />

          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (question.trim()) askMutation.mutate(question.trim());
            }}
          >
            <label htmlFor="analytics-question" className="text-sm font-medium">
              Ask a question about this dataset
            </label>
            <Textarea
              id="analytics-question"
              maxLength={1000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="e.g. What is total revenue by region?"
            />
            <div>
              <Button type="submit" disabled={!question.trim() || askMutation.isPending}>
                {askMutation.isPending ? "Asking…" : "Ask"}
              </Button>
            </div>
            {askMutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {messageFor(askMutation.error)}
              </p>
            )}
          </form>

          <div className="flex flex-col gap-4">
            {analyses.map((analysis) => (
              <AnalysisCard key={analysis.id} analysis={analysis} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
