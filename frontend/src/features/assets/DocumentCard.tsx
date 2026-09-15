import { useMutation, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { FileText, Trash2 } from "lucide-react";
import { useState } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { messageFor } from "@/lib/api-error";
import { formatBytes, formatRelativeTime } from "@/lib/format";
import { isSettled } from "@/features/assets/asset-state";
import { cn } from "@/lib/utils";
import * as assetsService from "@/services/assets";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];

interface DocumentCardProps {
  asset: AssetRead;
  isSelected: boolean;
  onSelect: () => void;
  projectId: string;
}

export function DocumentCard({ asset, isSelected, onSelect, projectId }: DocumentCardProps) {
  // "Still working" is the exact inverse of the settle check the
  // polling loop uses, so the indicator and the polling can never
  // disagree about whether this document is finished.
  const working = !isSettled(asset);

  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => assetsService.deleteAsset(asset.id),
    onSuccess: () => {
      setConfirmOpen(false);
      queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
    },
  });

  // A failed GENERATED report can be re-run; an uploaded document's
  // failure is not retryable from here (`reprocess` rejects reports).
  const retryable = asset.source === "generated" && asset.processing_status === "failed";
  const retryMutation = useMutation({
    mutationFn: () => reportsService.retryReport(asset.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["assets", projectId] }),
  });

  return (
    <motion.div whileHover={{ x: 2 }} transition={{ duration: 0.15 }}>
      <Card
        role="button"
        tabIndex={0}
        onClick={onSelect}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect();
          }
        }}
        aria-pressed={isSelected}
        className={cn(
          "cursor-pointer transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          isSelected
            ? "border-primary/60 bg-accent/30 shadow-raised"
            : "hover:border-border-strong hover:shadow-raised",
        )}
      >
        <CardContent className="flex items-start gap-3 p-4">
          <div
            className={cn(
              "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
              working ? "bg-ai-soft text-ai" : "bg-secondary text-muted-foreground",
              working && "ai-pulse-ring",
            )}
          >
            <FileText className="h-4 w-4" />
          </div>

          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="flex items-start justify-between gap-2">
              <p className="min-w-0 truncate text-sm font-medium">{asset.file_name}</p>
              {/* `stopPropagation` so this doesn't also trigger the
               * card's own `onSelect` -- the card's outer wrapper is
               * `role="button"` for selection, and this is a second,
               * independent action nested inside it. */}
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setConfirmOpen(true);
                }}
                className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span className="sr-only">Delete {asset.file_name}</span>
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge domain="assetProcessing" value={asset.processing_status} />
              <StatusBadge domain="aiProfile" value={asset.ai_profile.status} />
              <StatusBadge domain="embedding" value={asset.ai_profile.embedding_status} />
              {retryable && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-xs"
                  disabled={retryMutation.isPending}
                  onClick={(event) => {
                    // The card itself is `role="button"` for selection.
                    event.stopPropagation();
                    retryMutation.mutate();
                  }}
                >
                  {retryMutation.isPending ? "Retrying…" : "Retry"}
                </Button>
              )}
            </div>
            {retryMutation.isError && (
              <p role="alert" className="text-xs text-destructive">
                {messageFor(retryMutation.error)}
              </p>
            )}
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="tabular">{formatBytes(asset.file_size)}</span>
              <span>{formatRelativeTime(asset.created_at)}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Also stops propagation implicitly -- Radix Dialog renders
       * into a portal outside the card, so clicks inside it never
       * reach the card's own click handler. */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent onClick={(event) => event.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Delete "{asset.file_name}"?</DialogTitle>
            <DialogDescription>
              This permanently deletes the document, its stored file, and every chunk and
              embedding built from it. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
            >
              {deleteMutation.isPending ? "Deleting…" : "Delete Document"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.div>
  );
}
