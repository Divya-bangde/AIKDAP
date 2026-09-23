import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Info, Loader2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { Button } from "@/components/ui/button";
import { asSpans, highlightBoxes } from "@/features/research/source-highlights";
import { downloadAssetFile, getChunkLocation } from "@/services/assets";

// react-pdf 10 / pdfjs-dist 5 ship the worker as an ES module.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

function Loading() {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      Loading source…
    </div>
  );
}

function Failure({ message }: { message: string }) {
  return <p className="py-16 text-center text-sm text-muted-foreground">{message}</p>;
}

/** The PDF behind a knowledge-base citation, opened at the cited
 * passage with it highlighted. Default export so `SourceViewer` can
 * `React.lazy` it: this module is the only one importing pdf.js.
 *
 * Only the current page is rendered. Highlights are divs over the page
 * canvas, recomputed from the rendered width on every zoom or resize. */
export default function PdfSourcePane({ chunkId, assetId }: { chunkId: string; assetId: string }) {
  const location = useQuery({
    queryKey: ["chunk-location", chunkId],
    queryFn: () => getChunkLocation(chunkId),
  });
  // A Blob through the authenticated client; the query caches it, so
  // `file` keeps one identity across renders and pdf.js loads it once.
  const file = useQuery({
    queryKey: ["asset-file", assetId],
    queryFn: () => downloadAssetFile(assetId),
    staleTime: Infinity,
  });

  const containerRef = useRef<HTMLDivElement>(null);
  const firstBoxRef = useRef<HTMLDivElement>(null);
  const scrolledRef = useRef(false);
  const [containerWidth, setContainerWidth] = useState(0);
  const [numPages, setNumPages] = useState(0);
  const [pageOverride, setPageOverride] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const spans = asSpans(location.data?.spans ?? []);
  const targetPage = spans[0]?.page ?? location.data?.page_start ?? 1;
  const page = pageOverride ?? targetPage;
  const renderedWidth = Math.max(containerWidth * zoom, 1);
  const boxes = highlightBoxes(spans, page, renderedWidth);
  const matched = location.data !== undefined && location.data.match_quality !== "none";

  // Scroll the passage into view once, after its page first renders.
  function handleRenderSuccess() {
    if (scrolledRef.current || page !== targetPage) return;
    scrolledRef.current = true;
    firstBoxRef.current?.scrollIntoView({ block: "center" });
  }

  const changeZoom = (delta: number) =>
    setZoom((current) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, current + delta)));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2">
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setPageOverride(page - 1)}
            disabled={page <= 1}
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="tabular min-w-[6rem] text-center text-xs text-muted-foreground">
            Page {page}
            {numPages > 0 && ` of ${numPages}`}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setPageOverride(page + 1)}
            disabled={numPages === 0 || page >= numPages}
            aria-label="Next page"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => changeZoom(-ZOOM_STEP)}
            disabled={zoom <= MIN_ZOOM}
            aria-label="Zoom out"
          >
            <ZoomOut className="h-4 w-4" />
          </Button>
          <span className="tabular w-12 text-center text-xs text-muted-foreground">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => changeZoom(ZOOM_STEP)}
            disabled={zoom >= MAX_ZOOM}
            aria-label="Zoom in"
          >
            <ZoomIn className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {location.data && !matched && (
        <p className="mx-4 mt-3 flex items-center gap-2 rounded-lg bg-sunken px-3 py-2 text-xs text-muted-foreground">
          <Info className="h-3.5 w-3.5 shrink-0" />
          Exact passage not found. Showing the page.
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div ref={containerRef} className="w-full">
          {location.isError || file.isError ? (
            <Failure message="This source could not be loaded." />
          ) : !location.data || !file.data ? (
            <Loading />
          ) : (
            <Document
              file={file.data}
              onLoadSuccess={({ numPages: total }) => setNumPages(total)}
              loading={<Loading />}
              error={<Failure message="This file could not be opened as a PDF." />}
            >
              <div className="relative mx-auto w-fit shadow-float">
                <Page
                  pageNumber={page}
                  width={renderedWidth}
                  loading={<Loading />}
                  onRenderSuccess={handleRenderSuccess}
                />
                {boxes.length > 0 && (
                  <span className="sr-only">Cited passage highlighted on page {page}.</span>
                )}
                {boxes.map((box, index) => (
                  <div
                    key={index}
                    ref={index === 0 ? firstBoxRef : undefined}
                    aria-hidden
                    className="pointer-events-none absolute rounded-[2px] bg-warning/35 mix-blend-multiply"
                    style={box}
                  />
                ))}
              </div>
            </Document>
          )}
        </div>
      </div>
    </div>
  );
}
