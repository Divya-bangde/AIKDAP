import { FileText } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { Stagger, StaggerItem } from "@/components/motion/PageTransition";
import { Skeleton } from "@/components/ui/skeleton";
import { AiProfilePanel } from "@/features/assets/AiProfilePanel";
import { DocumentCard } from "@/features/assets/DocumentCard";
import { UploadDropzone } from "@/features/assets/UploadDropzone";
import { allSettled } from "@/features/assets/asset-state";
import { usePolling } from "@/hooks/usePolling";
import * as assetsService from "@/services/assets";

export function DocumentsSection({ projectId }: { projectId: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Polls only while at least one asset is still mid-pipeline —
  // extraction, Qwen understanding, or BGE-M3 embedding (Phase 38:
  // "avoid unnecessary API calls").
  const assetsQuery = usePolling({
    queryKey: ["assets", projectId],
    queryFn: () => assetsService.listAssets(projectId),
    isTerminal: allSettled,
  });

  const assets = assetsQuery.data ?? [];
  // Default to the newest document so the AI panel has something to
  // show the moment processing finishes, rather than requiring a click.
  const selectedAsset = assets.find((asset) => asset.id === selectedId) ?? assets[0];

  return (
    <div className="flex flex-col gap-5">
      <UploadDropzone projectId={projectId} />

      {assetsQuery.isLoading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      )}

      {assetsQuery.isError && <ErrorState error={assetsQuery.error} />}

      {assetsQuery.isSuccess && assets.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No documents uploaded yet."
          description="Upload a document to build this project's knowledge base."
        />
      )}

      {assetsQuery.isSuccess && assets.length > 0 && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <Stagger className="flex flex-col gap-2">
            {assets.map((asset) => (
              <StaggerItem key={asset.id}>
                <DocumentCard
                  asset={asset}
                  isSelected={asset.id === selectedAsset?.id}
                  onSelect={() => setSelectedId(asset.id)}
                  projectId={projectId}
                />
              </StaggerItem>
            ))}
          </Stagger>
          <div>
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
    </div>
  );
}
