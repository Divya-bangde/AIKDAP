import { FileText } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { LoadMore } from "@/components/common/LoadMore";
import { usePagedAssets } from "@/hooks/usePagedLists";
import { CrossPaperAnalysisPanel } from "@/features/research/CrossPaperAnalysisPanel";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { Stagger, StaggerItem } from "@/components/motion/PageTransition";
import { Skeleton } from "@/components/ui/skeleton";
import { AiProfilePanel } from "@/features/assets/AiProfilePanel";
import { DocumentCard } from "@/features/assets/DocumentCard";
import { UploadDropzone } from "@/features/assets/UploadDropzone";
import { isSettled, isSource } from "@/features/assets/asset-state";
import { usePolling } from "@/hooks/usePolling";
import * as assetsService from "@/services/assets";

/** A project's documents, or — with no `projectId` — every document the
 * user owns across all projects (upload needs a project, so it is hidden). */
export function DocumentsSection({ projectId }: { projectId?: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const assetsQuery = usePagedAssets(projectId);
  const assets = assetsQuery.items.filter(isSource);
  const selectedId = searchParams.get("primary") ?? assets[0]?.id;
  const selectedQuery = usePolling({
    queryKey: ["assets", "detail", selectedId],
    queryFn: () => assetsService.getAsset(selectedId!),
    isTerminal: isSettled,
    enabled: Boolean(selectedId),
  });
  const selectedAsset = selectedQuery.data;
  function select(id: string) {
    setSearchParams((params) => { const next = new URLSearchParams(params); next.set("primary", id); return next; }, { replace: true });
  }

  return (
    <div className="flex flex-col gap-5">
      {projectId && <UploadDropzone projectId={projectId} />}

      {assetsQuery.isLoading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      )}

      {assetsQuery.isError && !assetsQuery.isFetchNextPageError && <ErrorState error={assetsQuery.error} />}

      {assetsQuery.isSuccess && assets.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No documents uploaded yet."
          description={
            projectId
              ? "Upload a document to build this project's knowledge base."
              : "Open a project to upload its first document."
          }
        />
      )}

      {assets.length > 0 && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <Stagger className="flex flex-col gap-2">
            {assets.map((asset) => (
              <StaggerItem key={asset.id}>
                <DocumentCard
                  asset={asset}
                  isSelected={asset.id === selectedAsset?.id}
                  onSelect={() => select(asset.id)}
                />
              </StaggerItem>
            ))}
          </Stagger>
          <div>
            {selectedQuery.isError && <ErrorState error={selectedQuery.error} />}
            {selectedQuery.isLoading && <Skeleton className="h-40" />}
            {selectedAsset ? (
              <AiProfilePanel asset={selectedAsset} />
            ) : (
              <p className="p-4 text-sm text-muted-foreground">
                Select a document to view its AI-understanding results.
              </p>
            )}
          </div>
        </div>
      )}
      <LoadMore query={assetsQuery} label="documents" />
      {selectedAsset && selectedAsset.asset_type === "document" && (!projectId || selectedAsset.project_id === projectId) && <CrossPaperAnalysisPanel key={selectedAsset.id} primaryAssetId={selectedAsset.id} projectId={selectedAsset.project_id} papers={assets} />}
    </div>
  );
}
