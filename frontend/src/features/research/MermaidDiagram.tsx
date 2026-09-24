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

  useEffect(() => {
    let cancelled = false;
    // Mermaid bakes colours into the SVG, so a theme switch re-renders.
    // "strict" is the load-bearing setting: the source is model output, so
    // Mermaid must encode any HTML in labels and disable click handlers.
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral" });
    mermaid
      .render(id, source)
      .then(({ svg: rendered }) => {
        if (cancelled) return;
        setSvg(rendered);
        setFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id, source, dark]);

  if (failed) {
    // Invalid Mermaid from the model: show the source rather than nothing,
    // so the reader still gets the structure it describes.
    return (
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-sunken p-3 font-mono text-xs text-foreground">
        {source}
      </pre>
    );
  }
  if (!svg) return <p className="text-sm text-muted-foreground">Rendering diagram…</p>;

  // Sanitized by Mermaid itself under securityLevel "strict" (above).
  return (
    <div
      className="overflow-x-auto [&_svg]:mx-auto [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
