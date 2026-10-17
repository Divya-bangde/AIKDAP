import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2, Network, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type NodeObject } from "react-force-graph-2d";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useGraphColors } from "@/features/paper-map/graph-colors";
import { RenderGuard } from "@/features/research/RendererBoundary";
import { messageFor } from "@/lib/api-error";
import * as papersService from "@/services/papers";
import type { components } from "@/types/api";

type PaperNode = components["schemas"]["PaperNode"];
type GraphNode = NodeObject<PaperNode>;

const GRAPH_HEIGHT = 520;
/** Titles are drawn on the canvas only when zoomed in this far. */
const LABEL_ZOOM = 2.5;
/** How long to keep polling for an added paper to appear. */
const IMPORT_WATCH_MS = 3 * 60 * 1000;

const KIND_LABELS: Record<PaperNode["kind"], string> = {
  uploaded: "Uploaded",
  suggested: "Added from suggestions",
  external: "Outside paper",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
}

export function paperUrl(node: Pick<PaperNode, "id" | "doi">): string {
  return node.doi ? `https://doi.org/${node.doi}` : `https://openalex.org/${node.id}`;
}

/** Why there is no graph to draw, in the reader's terms. */
function emptyReason(matchedCount: number, showExternal: boolean): string {
  if (matchedCount === 0) return "No papers have an OpenAlex match yet. Unmatched papers are listed below; their citations are unknown.";
  return showExternal
    ? "Your papers don't cite each other, or shared outside papers, often enough to draw a map yet."
    : "Your matched papers don't cite each other yet. Try showing frequently cited outside papers.";
}

/** The project's citation graph: an arrow from each paper to the papers
 * it cites. Default export so the project page can lazy-load it and
 * keep the force-graph library out of the main bundle. */
export default function PaperMap({ projectId }: { projectId: string }) {
  const [showExternal, setShowExternal] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [paused, setPaused] = useState(false);
  const selectionOpener = useRef<HTMLElement | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // Outside papers queued for import -> when; polled until they join.
  const [adding, setAdding] = useState<Record<string, number>>({});
  const colors = useGraphColors();

  const graphQuery = useQuery({
    queryKey: ["paper-graph", projectId, showExternal],
    queryFn: () => papersService.getPaperGraph(projectId, showExternal),
    placeholderData: keepPreviousData,
    refetchInterval: () =>
      Object.values(adding).some((at) => Date.now() - at < IMPORT_WATCH_MS) ? 5000 : false,
  });
  const graph = graphQuery.data;

  useEffect(() => {
    if (!graph) return;
    const joined = new Set(graph.nodes.filter((node) => node.in_project).map((node) => node.id));
    setAdding((current) => {
      const next = Object.fromEntries(Object.entries(current).filter(([id]) => !joined.has(id)));
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
  }, [graph]);

  const importMutation = useMutation({
    mutationFn: (openalexId: string) => papersService.importOutsidePaper(projectId, openalexId),
    onSuccess: (_, openalexId) => setAdding((current) => ({ ...current, [openalexId]: Date.now() })),
  });

  // ForceGraph mutates the objects it is given (x, y, vx...), so it
  // gets copies, rebuilt only when the API data changes.
  const graphData = useMemo(
    () => ({
      nodes: (graph?.nodes ?? []).map((node) => ({ ...node })),
      links: (graph?.links ?? []).map((link) => ({ ...link })),
    }),
    [graph],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<GraphNode> | undefined>(undefined);
  const fittedRef = useRef(false);
  const [width, setWidth] = useState(0);
  const selectPaper = (id: string, opener?: HTMLElement) => {
    selectionOpener.current = opener ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    setSelectedId(id);
  };
  useEffect(() => {
    if (paused) graphRef.current?.pauseAnimation();
    else graphRef.current?.resumeAnimation();
  }, [paused, width]);
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [graph]);
  useEffect(() => {
    fittedRef.current = false;
  }, [graphData]);

  if (graphQuery.isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading paper map…
      </div>
    );
  }
  if (graphQuery.isError || !graph) {
    return <ErrorState error={graphQuery.error} title="The paper map could not be loaded" />;
  }

  const matchedCount = graph.nodes.filter((node) => node.in_project).length;
  const connectedIds = new Set(graph.links.flatMap((link) => [String(link.source), String(link.target)]));
  const isolatedCount = graph.nodes.filter((node) => !connectedIds.has(node.id)).length;
  const listedNodes = graph.nodes.filter((node) => `${node.title} ${node.year ?? ""}`.toLowerCase().includes(search.toLowerCase()));
  // Derived from the latest data, so an added paper flips to in-project
  // while its panel is open.
  const selected = graph.nodes.find((node) => node.id === selectedId) ?? null;
  const selectedAdding = selected !== null && selected.id in adding;
  const selectedTimedOut = selectedAdding && Date.now() - adding[selected.id] >= IMPORT_WATCH_MS;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={showExternal}
            onChange={(event) => setShowExternal(event.target.checked)}
            className="h-4 w-4"
          />
          Show frequently cited outside papers
          {graphQuery.isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </label>
        <ul className="flex flex-wrap gap-4 text-xs text-muted-foreground" aria-label="Legend">
          {(Object.keys(KIND_LABELS) as PaperNode["kind"][]).map((kind) => (
            <li key={kind} className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: colors[kind] }} />
              {KIND_LABELS[kind]}
            </li>
          ))}
        </ul>
      </div>

      {graph.external_unavailable && (
        <p className="rounded-lg bg-sunken px-3 py-2 text-xs text-muted-foreground">
          OpenAlex couldn't be reached, so outside papers aren't shown right now.
        </p>
      )}

      <p className="text-xs text-muted-foreground">Arrows point from the citing paper to the cited paper. Node size reflects global citation count, not relevance to your question. The searchable paper list provides the same details and source actions as canvas selection.</p>
      {graph.links.length > 0 && <div role="group" aria-label="Paper map view controls" className="flex flex-wrap gap-2">
        <button type="button" className="rounded border border-border px-3 py-2 text-xs" onClick={() => graphRef.current?.zoomToFit(300, 40)}>Fit paper map</button>
        <button type="button" className="rounded border border-border px-3 py-2 text-xs" onClick={() => { graphRef.current?.centerAt(0, 0, 300); graphRef.current?.zoomToFit(300, 40); }}>Reset paper map view</button>
        <button type="button" className="rounded border border-border px-3 py-2 text-xs" onClick={() => { const current = graphRef.current?.zoom(); if (typeof current === "number") graphRef.current?.zoom(Math.min(12, current * 1.3), 200); }}>Zoom in paper map</button>
        <button type="button" className="rounded border border-border px-3 py-2 text-xs" onClick={() => { const current = graphRef.current?.zoom(); if (typeof current === "number") graphRef.current?.zoom(Math.max(0.1, current / 1.3), 200); }}>Zoom out paper map</button>
        <button type="button" aria-pressed={paused} className="rounded border border-border px-3 py-2 text-xs" onClick={() => setPaused((value) => !value)}>{paused ? "Resume paper map animation" : "Pause paper map animation"}</button>
      </div>}

      {graph.links.length === 0 ? (
        <EmptyState
          icon={Network}
          title={graph.nodes.length === 0 ? "No papers on the map" : "No citation relationships found"}
          description={emptyReason(matchedCount, showExternal)}
        />
      ) : (
        <div
          ref={containerRef}
          className="overflow-hidden rounded-card bg-card shadow-subtle"
          role="img"
          aria-label={`Citation map of ${graph.nodes.length} papers with ${graph.links.length} citations. Select a paper to see its details.`}
        >
          {width > 0 && (
            <RenderGuard label="paper map"><ForceGraph2D<PaperNode>
              ref={graphRef}
              graphData={graphData}
              width={width}
              height={GRAPH_HEIGHT}
              nodeId="id"
              nodeVal={(node) => Math.max(1, Math.log((node.cited_by_count ?? 0) + 1))}
              nodeColor={(node) => colors[node.kind]}
              nodeLabel={(node) => escapeHtml(node.title)}
              linkColor={() => colors.link}
              linkDirectionalArrowLength={4}
              linkDirectionalArrowRelPos={1}
              nodeCanvasObjectMode={() => "after"}
              nodeCanvasObject={(node, ctx, scale) => {
                if (scale < LABEL_ZOOM || node.x === undefined || node.y === undefined) return;
                const title = node.title.length > 40 ? `${node.title.slice(0, 39)}…` : node.title;
                ctx.font = `${11 / scale}px sans-serif`;
                ctx.textAlign = "center";
                ctx.textBaseline = "top";
                ctx.fillStyle = colors.label;
                ctx.fillText(title, node.x, node.y + 6);
              }}
              cooldownTicks={120}
              onEngineStop={() => {
                if (fittedRef.current) return;
                fittedRef.current = true;
                graphRef.current?.zoomToFit(400, 40);
              }}
              onNodeClick={(node) => selectPaper(String(node.id))}
            /></RenderGuard>
          )}
        </div>
      )}

      {graph.links.length > 0 && <p className="text-xs text-muted-foreground">{graph.links.length} citation relationship{graph.links.length === 1 ? "" : "s"}. Arrows point from the citing paper to the cited paper.</p>}
      {isolatedCount > 0 && <p className="text-xs text-muted-foreground">{isolatedCount} isolated paper{isolatedCount === 1 ? " has" : "s have"} no citation relationship in the displayed graph; citations outside this scope may exist.</p>}
      {graph.links.length === 1 && <section aria-label="Citation relationship" className="rounded-lg border border-border p-3 text-sm">
        <p className="mb-2 font-medium">One citation relationship</p>
        {graph.links.map((link) => <div key={`${link.source}:${link.target}`} className="flex flex-wrap items-center gap-2">
          <button type="button" className="text-left underline" onClick={(event) => selectPaper(String(link.source), event.currentTarget)}>{graph.nodes.find((node) => node.id === link.source)?.title ?? String(link.source)}</button>
          <span>cites</span>
          <button type="button" className="text-left underline" onClick={(event) => selectPaper(String(link.target), event.currentTarget)}>{graph.nodes.find((node) => node.id === link.target)?.title ?? String(link.target)}</button>
        </div>)}
      </section>}

      {graph.nodes.length > 0 && <section aria-label="Accessible paper list" className="min-w-0 rounded-card border border-border p-4">
        <h3 className="text-sm font-medium">Papers on this map</h3>
        <label className="mt-3 flex flex-col gap-1 text-xs">Search papers by title or year<input ref={searchRef} type="search" value={search} onChange={(event) => setSearch(event.target.value)} className="w-full rounded border border-border bg-background px-3 py-2 text-sm" /></label>
        <p className="my-2 text-xs text-muted-foreground" role="status">{listedNodes.length} of {graph.nodes.length} papers shown. Search filters this list; the map keeps its full scope.</p>
        <ul className="flex max-h-96 flex-col gap-2 overflow-y-auto" aria-label="Papers on this map">{listedNodes.map((node) => <li key={node.id} className="rounded border border-border p-3 text-xs">
          <button type="button" onClick={(event) => selectPaper(node.id, event.currentTarget)} className="max-w-full break-words text-left text-sm font-medium underline">Inspect paper: {node.title}</button>
          <p className="mt-1 text-muted-foreground">{KIND_LABELS[node.kind]} · {node.year ?? "Year unknown"} · {node.cited_by_count ?? "Unknown"} global citations</p>
          <p className="mt-1 break-words text-muted-foreground">{connectedIds.has(node.id) ? `Cites ${graph.links.filter((link) => link.source === node.id).length}; cited by ${graph.links.filter((link) => link.target === node.id).length} in this map.` : "Isolated in this map; citations outside the displayed scope may exist."}</p>
        </li>)}</ul>
        {!listedNodes.length && <p className="text-sm">No papers match this search. Clear the search to inspect every paper.</p>}
      </section>}

      {graph.unmatched.length > 0 && (
        <section className="rounded-card bg-card p-5 shadow-subtle">
          <h3 className="text-sm font-medium text-foreground">Not on the map</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            These PDFs have no OpenAlex match{graph.unmatched.some((p) => p.status === "pending") && " yet"}, so
            their citations are unknown.
          </p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {graph.unmatched.map((paper) => (
              <li key={paper.paper_id} className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate text-foreground">{paper.title}</span>
                <Badge variant="muted">{paper.status === "pending" ? "Looking up" : "No match"}</Badge>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Sheet open={selected !== null} onOpenChange={(open) => !open && setSelectedId(null)}>
        <SheetContent className="max-w-md overflow-y-auto" onCloseAutoFocus={(event) => { event.preventDefault(); const opener = selectionOpener.current; (opener?.isConnected ? opener : searchRef.current)?.focus(); }}>
          {selected && (
            <div className="flex flex-col gap-4 p-6 pr-12">
              <div>
                <p className="text-label uppercase text-muted-foreground">{KIND_LABELS[selected.kind]}</p>
                <SheetTitle className="mt-1 break-words text-section">{selected.title}</SheetTitle>
                <SheetDescription className="sr-only">Details for this paper.</SheetDescription>
              </div>
              <dl className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <dt className="text-label uppercase text-muted-foreground">Year</dt>
                  <dd className="tabular mt-0.5">{selected.year ?? "Unknown"}</dd>
                </div>
                <div>
                  <dt className="text-label uppercase text-muted-foreground">Cited by</dt>
                  <dd className="tabular mt-0.5">{selected.cited_by_count ?? "Unknown"}</dd>
                </div>
              </dl>
              <a
                href={paperUrl(selected)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
              >
                Open paper
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
              {!selected.in_project && (
                <div className="flex flex-col gap-2">
                  <Button
                    onClick={() => importMutation.mutate(selected.id)}
                    disabled={importMutation.isPending || (selectedAdding && !selectedTimedOut)}
                    className="gap-2 self-start"
                  >
                    {importMutation.isPending || (selectedAdding && !selectedTimedOut) ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plus className="h-4 w-4" />
                    )}
                    {selectedAdding && !selectedTimedOut ? "Adding…" : "Add to project"}
                  </Button>
                  {selectedAdding && !selectedTimedOut && (
                    <p className="text-xs text-muted-foreground">
                      Downloading and processing the PDF. It joins the map as a project paper when
                      it's ready.
                    </p>
                  )}
                  {selectedTimedOut && (
                    <p className="text-xs text-muted-foreground">
                      Not added yet. Check the Documents tab; the PDF may not have been downloadable.
                    </p>
                  )}
                  {importMutation.isError && (
                    <p className="text-xs text-destructive">{messageFor(importMutation.error)}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
