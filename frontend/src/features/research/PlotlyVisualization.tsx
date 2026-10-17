import { useEffect, useMemo, useRef, useState } from "react";
import type * as PlotlyType from "plotly.js";

import { styledTraces, themedLayout, usePlotlyTheme } from "@/features/research/plotly-theme";
import { displayLayout } from "@/features/research/plotly-display";
import { loadPlotly, rendererDimension } from "@/features/research/visualization-registry";
import { preserveInteractionView, viewRevision } from "./plotly-view";

/** Renders model-produced Plotly traces. Default export so
 * `AnswerVisualization` can load it lazily. */
export default function PlotlyVisualization({
  data,
  layout,
}: {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
}) {
  const theme = usePlotlyTheme();
  const display = displayLayout(layout, data);
  const targetRef = useRef<HTMLDivElement>(null);
  const engine = useRef<typeof PlotlyType | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const active = useRef(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [ready, setReady] = useState(false);
  const readyRef = useRef(false);
  const specIdentity = useMemo(() => JSON.stringify({ data, layout }), [data, layout]);
  const failedFor = useRef<string | null>(null);

  useEffect(() => {
    active.current = true;
    const target = targetRef.current;
    let frame = 0;
    const displayed = () => !!target?.isConnected && target.getClientRects().length > 0 && target.clientWidth > 0 && target.clientHeight > 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!active.current || !engine.current || !readyRef.current || !displayed()) return;
        Promise.resolve().then(() => { if (active.current && displayed()) return engine.current?.Plots.resize(target!); }).catch(() => {
          // A hidden/unmounted chart during a transition is not a failed answer.
          if (active.current && displayed()) setError("The chart could not resize. Retry the renderer; plotted data remains available below.");
        });
      });
    });
    if (target?.parentElement) observer.observe(target.parentElement);
    return () => {
      active.current = false; observer.disconnect(); cancelAnimationFrame(frame);
      readyRef.current = false;
      void queue.current.then(() => { if (target && engine.current) { try { engine.current.purge(target); } catch { /* Already purged. */ } } });
    };
  }, []);
  useEffect(() => {
    const target = targetRef.current;
    const requestKey = `${attempt}:${specIdentity}`;
    if (failedFor.current === requestKey) return;
    queue.current = queue.current.then(async () => {
      if (!active.current || !target?.isConnected) return;
      const dimension = rendererDimension(data);
      const runtime = await loadPlotly(dimension);
      if (!active.current || !target.isConnected) return;
      engine.current = runtime;
      target.dataset.chartRendering = "true";
      target.dataset.chartRevision = String(Number(target.dataset.chartRevision ?? 0) + 1);
      const revision = viewRevision(layout, data);
      const themed = themedLayout(theme, { autosize: true, margin: { l: 72, r: 24, t: 24, b: 72 }, ...display.layout, uirevision: revision });
      if (dimension === "3d") themed.scene = { ...(themed.scene as Record<string, unknown>), uirevision: revision };
      const renderedLayout = target.dataset.chartViewRevision === revision ? preserveInteractionView(themed, (target as HTMLDivElement & { layout?: Record<string, unknown> }).layout, dimension) : themed;
      await runtime.react(target, styledTraces(theme, data) as PlotlyType.Data[], renderedLayout as Partial<PlotlyType.Layout>, { displaylogo: false, displayModeBar: false, responsive: false });
      target.dataset.chartViewRevision = revision;
      target.dataset.chartSpec = specIdentity;
      target.dataset.chartRendering = "false";
      if (active.current) { readyRef.current = true; setReady(true); setError(null); }
      failedFor.current = null;
    }).catch(() => { failedFor.current = requestKey; if (active.current) setError("The chart renderer could not load or draw this visualization. Inspect the plotted values or sources below, or retry once."); });
  }, [specIdentity, theme, attempt]);
  const setView = (fit: boolean) => {
    const target = targetRef.current;
    const axis = (name: "xaxis" | "yaxis") => (layout[name] ?? {}) as Record<string, unknown>;
    const is3d = data.some((trace) => ["surface", "scatter3d"].includes(String(trace.type)));
    const update = is3d ? {} : Object.fromEntries((["xaxis", "yaxis"] as const).flatMap((name) => {
      const range = !fit && Array.isArray(axis(name).range) ? axis(name).range : null;
      return [[`${name}.range`, range], [`${name}.autorange`, range === null]];
    }));
    if (is3d) {
      const scene = (layout.scene ?? {}) as Record<string, unknown>;
      for (const name of ["xaxis", "yaxis", "zaxis"]) {
        const supplied = (scene[name] ?? {}) as Record<string, unknown>;
        const range = !fit && Array.isArray(supplied.range) ? supplied.range : null;
        update[`scene.${name}.range`] = range;
        update[`scene.${name}.autorange`] = range === null;
      }
      Object.assign(update, { "scene.camera": { eye: { x: 1.25, y: 1.25, z: 1.25 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0, y: 0, z: 0 } } });
    }
    void Promise.resolve().then(() => { if (active.current && target?.isConnected && engine.current) return engine.current.relayout(target, update as Partial<PlotlyType.Layout>); }).catch(() => { if (active.current) setError("The chart view could not be updated. Retry the renderer or inspect the plotted data."); });
  };
  return (
    <div className="min-w-0 max-w-full space-y-2">
    <div role="group" aria-label="Chart view controls" className="flex flex-wrap gap-2"><button type="button" disabled={!ready || !!error} onClick={() => setView(true)} className="rounded border border-border px-3 py-2 text-xs">Fit chart to data</button><button type="button" disabled={!ready || !!error} onClick={() => setView(false)} className="rounded border border-border px-3 py-2 text-xs">Reset chart view</button></div>
    {error && <div role="status" className="rounded border border-border p-3 text-sm"><p>{error}</p><button type="button" disabled={attempt >= 1} onClick={() => { setError(null); setAttempt((value) => value + 1); }} className="mt-2 rounded border border-border px-3 py-2 text-xs">{attempt >= 1 ? "Renderer retry used; reload the page to try again" : "Retry chart renderer"}</button></div>}
    {display.abbreviated && <p className="text-xs text-muted-foreground">Long axis labels or titles are abbreviated. Full labels and units remain in chart context, plotted values and data exports.</p>}
    <div role="region" aria-label="Interactive chart; horizontal scrolling available" tabIndex={0} className="max-w-full overflow-x-auto">
    <div style={{ minWidth: 420 }}>
    <div ref={targetRef} style={{ width: "100%", height: 380, visibility: error ? "hidden" : "visible" }} />
    </div>
    </div>
    </div>
  );
}
