import { useMutation } from "@tanstack/react-query";
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
import { Input } from "@/components/ui/input";
import { formatBytes } from "@/lib/format";
import { isApiError, messageFor } from "@/lib/api-error";
import * as analyticsService from "@/services/analytics";
import type { KaggleFileRead } from "@/services/analytics";

export function KaggleImportDialog({
  open,
  onOpenChange,
  projectId,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onImported: () => void;
}) {
  const [ref, setRef] = useState("");
  const [files, setFiles] = useState<KaggleFileRead[] | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [imported, setImported] = useState(false);

  const [owner, dataset] = ref.split("/").map((part) => part.trim());
  const refValid = Boolean(owner && dataset);

  const searchMutation = useMutation({
    mutationFn: () => analyticsService.listKaggleFiles(owner, dataset),
    onSuccess: (result) => setFiles(result),
  });

  const importMutation = useMutation({
    mutationFn: (name: string) => analyticsService.importKaggle(projectId, owner, dataset, name),
    onSuccess: () => {
      setImported(true);
      onImported();
    },
  });

  function handleOpenChange(next: boolean) {
    if (!next) {
      setRef("");
      setFiles(null);
      setFileName(null);
      setImported(false);
      searchMutation.reset();
      importMutation.reset();
    }
    onOpenChange(next);
  }

  const notConfigured = isApiError(searchMutation.error) && searchMutation.error.status === 503;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import from Kaggle</DialogTitle>
          <DialogDescription>
            Enter a Kaggle dataset reference (owner/dataset) to browse its files and import one.
          </DialogDescription>
        </DialogHeader>

        {!imported && (
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Dataset</span>
              <div className="flex gap-2">
                <Input
                  placeholder="owner/dataset"
                  value={ref}
                  onChange={(event) => {
                    setRef(event.target.value);
                    setFiles(null);
                    setFileName(null);
                    searchMutation.reset();
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={!refValid || searchMutation.isPending}
                  onClick={() => searchMutation.mutate()}
                >
                  {searchMutation.isPending ? "Searching…" : "Find files"}
                </Button>
              </div>
            </label>

            {searchMutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {notConfigured ? "Kaggle import isn't configured on this server." : messageFor(searchMutation.error)}
              </p>
            )}

            {files && files.length > 0 && (
              <div className="flex max-h-56 flex-col gap-2 overflow-y-auto">
                {files.map((file) => (
                  <label
                    key={file.name}
                    className="flex cursor-pointer items-center gap-3 rounded-md border border-border p-3"
                  >
                    <input
                      type="radio"
                      name="kaggle-file"
                      checked={fileName === file.name}
                      onChange={() => setFileName(file.name)}
                      className="h-4 w-4"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{file.name}</span>
                    </span>
                    <span className="text-xs text-muted-foreground">{formatBytes(file.size)}</span>
                  </label>
                ))}
              </div>
            )}

            {files && files.length === 0 && (
              <p className="text-sm text-muted-foreground">No files found for this dataset.</p>
            )}

            {importMutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {messageFor(importMutation.error)}
              </p>
            )}
          </div>
        )}

        {imported && (
          <p role="status" className="text-sm text-muted-foreground">
            Import started — the dataset appears when processing finishes.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Close
          </Button>
          {!imported && (
            <Button
              disabled={!fileName || importMutation.isPending}
              onClick={() => fileName && importMutation.mutate(fileName)}
            >
              {importMutation.isPending ? "Importing…" : "Import"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
