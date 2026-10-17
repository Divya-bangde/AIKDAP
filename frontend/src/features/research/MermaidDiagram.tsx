import mermaid from "mermaid";
import { useEffect, useId, useState } from "react";

import { useThemeValue } from "@/lib/theme-colors";

const isDarkTheme = () => document.documentElement.classList.contains("dark");

/** Renders model-produced Mermaid source. Default export so
 * `AnswerVisualization` can load it lazily. */
export default function MermaidDiagram({ source }: { source: string }) {
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const dark = useThemeValue(isDarkTheme);
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [naturalWidth, setNaturalWidth] = useState(640);
  const [attempt, setAttempt] = useState(0);
  const overBudget = source.length > 20000 || source.split("\n").length > 500 || (source.match(/-->|---|==>|-\.->|->>/g)?.length ?? 0) > 300;

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setFailed(false);
    if (overBudget) { setFailed(true); return; }
    // Mermaid bakes colours into the SVG, so a theme switch re-renders.
    // "strict" is the load-bearing setting: the source is model output, so
    // Mermaid must encode any HTML in labels and disable click handlers.
    Promise.resolve().then(() => {
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral", maxTextSize: 20000, maxEdges: 300 });
      return mermaid.render(id, source);
    })
      .then(({ svg: rendered }) => {
        if (cancelled) return;
        setSvg(rendered);
        const root = new DOMParser().parseFromString(rendered, "image/svg+xml").documentElement;
        const viewWidth = Number(root.getAttribute("viewBox")?.split(/[\s,]+/)[2]);
        setNaturalWidth(Number.isFinite(viewWidth) && viewWidth > 0 ? Math.max(640, viewWidth) : 640);
        setFailed(false);
      })
      .catch(() => {
        document.getElementById(`d${id}`)?.remove();
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id, source, dark, attempt, overBudget]);

  if (failed) {
    // Invalid Mermaid from the model: show the source rather than nothing,
    // so the reader still gets the structure it describes.
    return <div className="space-y-2"><p role="status" className="text-sm">{overBudget ? "Diagram exceeds the rendering budget (20,000 characters, 500 lines or 300 relationships). Simplify it or inspect the source below." : "The diagram could not render. Its relationship source remains available below; retry once or inspect the text alternative."}</p>
      {!overBudget && <button type="button" disabled={attempt >= 1} onClick={() => setAttempt(1)} className="rounded border border-border px-3 py-2 text-xs">{attempt >= 1 ? "Diagram retry used; reload the page to try again" : "Retry diagram renderer"}</button>}
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-sunken p-3 font-mono text-xs text-foreground">
        {source}
      </pre>
    </div>;
  }
  if (!svg) return <p className="text-sm text-muted-foreground">Rendering diagram…</p>;

  // Sanitized by Mermaid itself under securityLevel "strict" (above).
  return <div className="min-w-0 space-y-2">
    <div className="flex flex-wrap gap-2" role="group" aria-label="Diagram view controls">
      <button type="button" onClick={() => setZoom((value) => Math.min(3, value + 0.25))} className="rounded border border-border px-3 py-2 text-xs">Zoom in diagram</button>
      <button type="button" onClick={() => setZoom((value) => Math.max(0.75, value - 0.25))} className="rounded border border-border px-3 py-2 text-xs">Zoom out diagram</button>
      <button type="button" onClick={() => setZoom(1)} className="rounded border border-border px-3 py-2 text-xs">Reset diagram view</button>
    </div>
    <p className="text-xs text-muted-foreground">Diagram zoom {Math.round(zoom * 100)}%. Scroll this view horizontally to read every label; use the text relationship alternative below.</p>
    <div
      role="region" aria-label="Scrollable diagram" tabIndex={0}
      className="max-w-full overflow-auto rounded border border-border [&_svg]:max-w-none [&_svg]:h-auto"
      style={{ maxHeight: "70vh" }}
    ><div style={{ width: `${naturalWidth * zoom}px`, minWidth: "100%" }}
      className="[&_svg]:!w-full [&_svg]:!max-w-none"
      dangerouslySetInnerHTML={{ __html: svg }}
    /></div>
  </div>;
}
