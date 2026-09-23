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
  if (matchedCount < 2) {
    return "Fewer than two of this project's PDFs are matched in OpenAlex, so there are no citations to connect. Papers with a DOI on their first pages match most reliably.";
  }
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

      {graph.links.length < 2 ? (
        <EmptyState
          icon={Network}
          title="Not enough citations to map yet"
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
            <ForceGraph2D<PaperNode>
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
              onNodeClick={(node) => setSelectedId(String(node.id))}
            />
          )}
        </div>
      )}

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
        <SheetContent className="max-w-md">
          {selected && (
            <div className="flex flex-col gap-4 p-6 pr-12">
              <div>
                <p className="text-label uppercase text-muted-foreground">{KIND_LABELS[selected.kind]}</p>
                <SheetTitle className="mt-1 text-section">{selected.title}</SheetTitle>
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
