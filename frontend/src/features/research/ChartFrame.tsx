import { useId, useMemo, useRef, useState, type ReactNode } from "react";

import { downloadFile, fileSlug } from "@/lib/export";
import { contextualSvg, svgFromDataUrl } from "./visualization-export";
import { loadPlotly, rendererDimension } from "./visualization-registry";
import { readThemeColor } from "@/lib/theme-colors";

export type ChartSource = { id: string; title: string; onOpen: () => void; url?: string; reference?: string; asset_id?: string; chunk_id?: string };
type Trace = Record<string, unknown>;
type ValueRow = { trace: string; point: number; x?: unknown; y?: unknown; z?: unknown; label?: unknown; value?: unknown };
export type ChartContext = { title: string; finding?: string; origin: string; scope?: string[]; context?: string[]; sources?: ChartSource[]; metadata?: Record<string, unknown> };

/** A bounded, deterministic description. Every range stays with its own
 * trace; no comparison or units are inferred across quantities. */
export function chartFinding(data: Trace[]): string {
  const summaries = data.slice(0, 4).map((trace, index) => {
    const raw = trace.type === "pie" ? trace.values : ["heatmap", "contour", "surface"].includes(String(trace.type)) ? (trace.z as unknown[][] | undefined)?.flat() : trace.y;
    const values = Array.isArray(raw) ? raw : [];
    const numeric = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
    const missing = values.filter((value) => value === null).length;
    let min = Infinity; let max = -Infinity;
    for (const value of numeric) { min = Math.min(min, value); max = Math.max(max, value); }
    return `${String(trace.name || `Trace ${index + 1}`)}: ${values.length} values${numeric.length ? `, displayed range ${min} to ${max}` : ""}${missing ? `, ${missing} missing (gaps)` : ""}`;
  });
  return `${summaries.join("; ")}${data.length > 4 ? `; ${data.length - 4} additional series available in the values table` : ""}.`;
}

/** Plot-aligned values, including null gaps and explicit grid coordinates. */
export function plottedRows(data: Trace[]): ValueRow[] {
  return data.flatMap<ValueRow>((trace, index) => {
    const name = String(trace.name || `Trace ${index + 1}`);
    const array = (key: string): unknown[] => Array.isArray(trace[key]) ? trace[key] as unknown[] : [];
    if (trace.type === "pie") return array("values").map((value, point) => ({ trace: name, point, label: array("labels")[point], value }));
    if (["heatmap", "contour", "surface"].includes(String(trace.type))) return array("z").flatMap((row, rowIndex) => Array.isArray(row) ? row.map((z, columnIndex) => ({ trace: name, point: rowIndex * row.length + columnIndex, x: array("x").length ? array("x")[columnIndex] : columnIndex, y: array("y").length ? array("y")[rowIndex] : rowIndex, z })) : []);
    return array("x").map((x, point) => ({ trace: name, point, x, y: array("y")[point], ...(trace.type === "scatter3d" ? { z: array("z")[point] } : {}) }));
  });
}

export function chartExport(context: ChartContext, data: Trace[], layout?: Trace, relationshipSource?: string, receivedData = data) {
  const mapping = data.map((trace, exported_trace) => ({ exported_trace, received_trace: receivedData.indexOf(trace), uid: trace.uid ?? null, name: trace.name ?? null }));
  const rawProof = context.metadata?.provenance;
  const proof = Array.isArray(rawProof) ? rawProof.flatMap((point) => {
    if (!point || typeof point !== "object") return [];
    const reference = point as Trace;
    const mapped = mapping.find((item) => item.received_trace === reference.trace);
    return mapped ? [{ ...reference, trace: mapped.exported_trace, received_trace: mapped.received_trace }] : [];
  }) : undefined;
  return {
    title: context.title, finding: context.finding ?? null, origin: context.origin,
    scope: context.scope ?? [], context: context.context ?? [],
    sources: (context.sources ?? []).map(({ onOpen: _onOpen, ...source }) => source),
    metadata: { ...(context.metadata ?? {}), ...(proof ? { provenance: proof, received_provenance: rawProof } : {}) }, layout: layout ?? {},
    plotted_trace_mapping: mapping,
    plotted_traces: data, plotted_values: plottedRows(data), relationship_source: relationshipSource ?? null,
  };
}

function axisText(axis: unknown): string | null {
  if (!axis || typeof axis !== "object") return null;
  const title = (axis as Trace).title;
  return typeof title === "string" ? title : title && typeof title === "object" && typeof (title as Trace).text === "string" ? (title as Trace).text as string : null;
}

const PAGE_SIZE = 200;
const cellText = (value: unknown) => value === null ? "Missing (gap)" : value === undefined ? "—" : String(value);

/** Shared context/table/export shell. Selection is applied identically to
 * renderer, table and export. Full received data remains inspectable separately. */
export function ChartFrame({ children, data = [], layout = {}, relationshipSource, ...context }: ChartContext & {
  data?: Trace[]; layout?: Trace; relationshipSource?: string; children: (data: Trace[]) => ReactNode;
}) {
  const id = useId();
  const frameRef = useRef<HTMLElement>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [tableOpen, setTableOpen] = useState(false);
  const [page, setPage] = useState(0);
  const [exportError, setExportError] = useState<string | null>(null);
  const nameCounts = new Map<string, number>();
  for (const trace of data) if (trace.name) nameCounts.set(String(trace.name), (nameCounts.get(String(trace.name)) ?? 0) + 1);
  const keyOf = (trace: Trace, index: number) => String(trace.uid ?? (trace.name && nameCounts.get(String(trace.name)) === 1 ? `name:${trace.name}` : `position:${trace.name ?? "trace"}-${index}`));
  const selected = useMemo(() => data.filter((trace, index) => !hidden.has(keyOf(trace, index))), [data, hidden]);
  const rows = useMemo(() => tableOpen ? plottedRows(selected) : [], [tableOpen, data, hidden]);
  const columns = ["trace", "point", "x", "y", "z", "label", "value"].filter((column) => column === "trace" || column === "point" || rows.some((row) => column in row));
  const axes = [axisText(layout.xaxis), axisText(layout.yaxis), ...Object.values((layout.scene as Trace | undefined) ?? {}).map(axisText)].filter(Boolean);
  const selectionScope = selected.length !== data.length ? [`Series selection: showing ${selected.length} of ${data.length} received traces.`] : [];
  const visiblePage = Math.min(page, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1));
  const finding = context.finding ?? (relationshipSource ? "Relationship diagram: labelled nodes and directed connections are described in the text alternative." : chartFinding(selected));
  const exportContext = { ...context, finding, scope: [...(context.scope ?? []), ...selectionScope] };
  const exportData = async () => {
    try {
      await downloadFile(`${fileSlug(context.title)}-data-and-context.json`, JSON.stringify(chartExport(exportContext, selected, layout, relationshipSource, data), null, 2), "application/json");
      setExportError(null);
    } catch { setExportError("Export failed. Retry the download or inspect the values below."); }
  };
  const exportImage = async () => {
    try {
      const diagram = frameRef.current?.querySelector('[aria-label="Scrollable diagram"] svg');
      let svg: string;
      if (relationshipSource && diagram) svg = new XMLSerializer().serializeToString(diagram);
      else {
        const target = frameRef.current?.querySelector<HTMLElement>(".js-plotly-plot");
        if (!target) throw new Error("No rendered chart is available.");
        const expected = JSON.stringify({ data: selected, layout });
        const revision = target.dataset.chartRevision;
        const current = () => target.isConnected && target.dataset.chartRendering === "false" && target.dataset.chartSpec === expected && target.dataset.chartRevision === revision;
        if (!current()) throw new Error("The chart is still updating. Retry image export after rendering settles.");
        const runtime = await loadPlotly(rendererDimension(selected));
        if (!current()) throw new Error("The chart changed while the image was being prepared.");
        svg = svgFromDataUrl(await runtime.toImage(target, { format: "svg", width: 1000, height: 600 }));
        if (!current()) throw new Error("The chart changed while the image was being exported.");
      }
      const imageContext = { ...exportContext, context: [...(exportContext.context ?? []), ...(axes.length ? [`Axes / units: ${axes.join("; ")}`] : [])] };
      const output = contextualSvg(svg, imageContext, chartExport(exportContext, selected, layout, relationshipSource, data), { ink: readThemeColor("--foreground"), background: readThemeColor("--background") });
      await downloadFile(`${fileSlug(context.title)}-with-context.svg`, output, "image/svg+xml");
      setExportError(null);
    } catch { setExportError("Image export failed. Retry after the renderer is available, or download the plotted data and context."); }
  };
  return <figure ref={frameRef} aria-labelledby={`${id}-title`} aria-describedby={`${id}-context`} aria-details={`${id}-values`} className="flex min-w-0 max-w-full flex-col gap-3 overflow-hidden rounded-lg border border-border bg-sunken/40 p-3 sm:p-4">
    <figcaption id={`${id}-title`} className="break-words text-sm font-medium text-foreground">{context.title}</figcaption>
    <div id={`${id}-context`} className="space-y-1 break-words text-xs text-muted-foreground">
      {finding && <p>{finding}</p>}
      <p>Data origin: {context.origin}.</p>
      {!!axes.length && <p>Axes / units: {axes.join("; ")}.</p>}
      {!axes.length && !relationshipSource && <p>Axis titles were not supplied; any declared units are listed with provenance.</p>}
      {[...(context.context ?? []), ...(context.scope ?? []), ...selectionScope].map((item, index) => <p key={index}>{item}</p>)}
    </div>
    {!!context.sources?.length && <nav aria-label="Chart evidence" className="flex flex-wrap gap-2">{context.sources.map((source) => <button key={source.id} type="button" onClick={source.onOpen} className="max-w-full break-words text-left text-xs text-primary underline">Open chart evidence: {source.title}</button>)}</nav>}
    {data.length > 1 && <details><summary className="cursor-pointer text-xs font-medium">Select series ({selected.length} of {data.length})</summary><fieldset className="mt-2 grid max-h-60 gap-2 overflow-y-auto sm:grid-cols-2"><legend className="sr-only">Visible chart series</legend>{data.map((trace, index) => <label key={keyOf(trace, index)} className="flex items-start gap-2 break-words text-xs"><input type="checkbox" checked={!hidden.has(keyOf(trace, index))} onChange={() => { const key = keyOf(trace, index); setHidden((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; }); setPage(0); }} />{String(trace.name || `Trace ${index + 1}`)}</label>)}</fieldset></details>}
    {selected.length || relationshipSource ? children(selected) : <p role="status" className="text-sm">No series selected. Select a series to display the chart.</p>}
    <div className="flex flex-wrap gap-2"><button type="button" onClick={exportData} className="rounded border border-border px-3 py-2 text-xs">Download plotted data and context</button><button type="button" onClick={exportImage} className="rounded border border-border px-3 py-2 text-xs">{relationshipSource ? "Download diagram (SVG with context)" : "Download chart image (SVG with context)"}</button></div>
    {exportError && <p role="status" className="text-sm">{exportError}</p>}
    {!!data.length && <details id={`${id}-values`} onToggle={(event) => setTableOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-xs font-medium">Inspect plotted values</summary>
      <p className="my-2 text-xs text-muted-foreground">Values match the visible traces, including missing-value gaps. Point indexes start at zero. Series names match the legend.</p>
      {tableOpen && <>
        <div role="region" aria-label={`${context.title}: plotted values`} tabIndex={0} className="max-h-80 max-w-full overflow-auto rounded border border-border">
          <table className="w-full text-left text-xs"><caption className="sr-only">{context.title}: exact plotted values</caption><thead><tr>{columns.map((column) => <th key={column} scope="col" className="border-b border-border p-2 font-medium">{column}</th>)}</tr></thead><tbody>{rows.slice(visiblePage * PAGE_SIZE, (visiblePage + 1) * PAGE_SIZE).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column} className="max-w-64 break-words border-b border-border p-2">{cellText(row[column as keyof ValueRow])}</td>)}</tr>)}</tbody></table>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs"><p>{rows.length ? `${visiblePage * PAGE_SIZE + 1}–${Math.min((visiblePage + 1) * PAGE_SIZE, rows.length)}` : "0"} of {rows.length} plotted values</p>{rows.length > PAGE_SIZE && <><button type="button" disabled={visiblePage === 0} onClick={() => setPage(visiblePage - 1)} className="rounded border border-border px-2 py-1">Previous values</button><button type="button" disabled={(visiblePage + 1) * PAGE_SIZE >= rows.length} onClick={() => setPage(visiblePage + 1)} className="rounded border border-border px-2 py-1">Next values</button></>}</div>
      </>}
    </details>}
    {selected.length !== data.length && <details><summary className="cursor-pointer text-xs">Inspect all received series</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(data, null, 2)}</pre></details>}
    {relationshipSource && <details id={`${id}-values`}><summary className="cursor-pointer text-xs font-medium">Diagram relationships (text alternative)</summary><p className="my-2 text-xs text-muted-foreground">The following source describes the diagram's labelled nodes and connections. Arrows describe the relationship direction.</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{relationshipSource}</pre></details>}
  </figure>;
}
