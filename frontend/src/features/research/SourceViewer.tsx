import { lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";

import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import type { Citation } from "@/types/citation";

// pdf.js is large; it loads the first time a source is opened, never
// with the main bundle.
const PdfSourcePane = lazy(() => import("@/features/research/PdfSourcePane"));

/** A knowledge-base citation that can open in the PDF viewer: it names
 * its chunk and asset, and the asset is a PDF. */
export function isPdfSource(citation: Citation): boolean {
  return Boolean(
    citation.chunk_id && citation.asset_id && citation.file_name?.toLowerCase().endsWith(".pdf"),
  );
}

/** Click-to-source: the cited PDF in a right-side sheet, opened at the
 * cited passage. */
export function SourceViewer({
  citation,
  onClose,
}: {
  citation: Citation | null;
  onClose: () => void;
}) {
  return (
    <Sheet open={citation !== null} onOpenChange={(next) => !next && onClose()}>
      <SheetContent className="max-w-3xl">
        <div className="min-w-0 px-6 pb-3 pr-12 pt-6">
          <p className="text-label uppercase text-muted-foreground">
            Source {citation?.id ? `· ${citation.id}` : ""}
          </p>
          <SheetTitle className="mt-1 truncate text-section">
            {citation?.title ?? citation?.file_name ?? "Source document"}
          </SheetTitle>
          <SheetDescription className="sr-only">
            The cited document, opened at the highlighted passage.
          </SheetDescription>
        </div>
        {citation?.chunk_id && citation.asset_id && (
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            }
          >
            <PdfSourcePane
              key={citation.chunk_id}
              chunkId={citation.chunk_id}
              assetId={citation.asset_id}
            />
          </Suspense>
        )}
      </SheetContent>
    </Sheet>
  );
}
