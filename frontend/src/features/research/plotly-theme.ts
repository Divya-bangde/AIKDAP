import { readThemeColor, useThemeValue } from "@/lib/theme-colors";

/** The concrete colours Plotly needs; it cannot read CSS variables. */
export interface PlotlyTheme {
  ink: string;
  colorway: string[];
}

const GRID = "rgba(128, 128, 128, 0.2)";
const ZERO_LINE = "rgba(128, 128, 128, 0.35)";

function readPlotlyTheme(): PlotlyTheme {
  return {
    ink: readThemeColor("--foreground"),
    colorway: Array.from({ length: 8 }, (_, i) => readThemeColor(`--chart-${i + 1}`)),
  };
}

/** Plotly colours from the live theme; re-renders the chart on a theme switch. */
export function usePlotlyTheme(): PlotlyTheme {
  return useThemeValue(readPlotlyTheme);
}

const DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"];
const SYMBOLS = ["circle", "square", "diamond", "cross", "triangle-up", "triangle-down", "star", "hexagon"];
const PATTERNS = ["/", "\\", "x", "-", "|", "+", "."];

/** Identity is independent of array order or currently visible series. */
export function seriesStyle(identity: string, theme: PlotlyTheme) {
  let hash = 2166136261;
  for (let i = 0; i < identity.length; i++) hash = Math.imul(hash ^ identity.charCodeAt(i), 16777619) >>> 0;
  return {
    color: theme.colorway[hash % theme.colorway.length],
    dash: DASHES[Math.floor(hash / 8) % DASHES.length],
    symbol: SYMBOLS[Math.floor(hash / 48) % SYMBOLS.length],
    pattern: PATTERNS[Math.floor(hash / 8) % PATTERNS.length],
  };
}

/** Marks have redundant shape/style encoding. Explicit curve/reference styles
 * retain their meaning; generated traces receive stable theme defaults. */
export function styledTraces(theme: PlotlyTheme, data: Record<string, unknown>[]): Record<string, unknown>[] {
  return data.map((trace, index) => {
    const key = String(trace.uid ?? trace.name ?? `unnamed-series-${index + 1}`);
    const style = seriesStyle(key, theme);
    const marker = (trace.marker ?? {}) as Record<string, unknown>;
    if (trace.type === "bar") return { ...trace, marker: { color: style.color, ...marker, line: { color: theme.ink, width: 1, ...(marker.line as object) }, pattern: { shape: style.pattern, ...(marker.pattern as object) } } };
    if (trace.type === "pie") {
      const categories = (trace.labels ?? []) as unknown[];
      const styles = categories.map(label => seriesStyle(JSON.stringify([typeof label, label]), theme));
      return { ...trace, marker: { colors: styles.map(s => s.color), ...marker, line: { color: theme.ink, width: 1, ...(marker.line as object) }, pattern: { shape: styles.map(s => s.pattern), ...(marker.pattern as object) } } };
    }
    if (trace.type === "scatter" || trace.type === "scatter3d" || !trace.type) {
      const line = (trace.line ?? {}) as Record<string, unknown>;
      const symbol = trace.type === "scatter3d" ? ["circle", "square", "diamond", "cross", "x"][SYMBOLS.indexOf(style.symbol) % 5] : style.symbol;
      return { ...trace, line: { color: style.color, dash: typeof line.color === "string" ? "solid" : style.dash, width: 2.5, ...line }, marker: { color: line.color ?? style.color, symbol, size: 7, ...marker, line: { color: theme.ink, width: 1, ...(marker.line as object) } } };
    }
    return trace;
  });
}

type Layout = Record<string, unknown>;

function mergeAxis(base: Layout, axis: unknown): Layout {
  return { ...base, ...(axis as Layout | undefined) };
}

/**
 * Applies the theme to a chart layout. The layout's own settings win,
 * except axes, which are merged rather than replaced so an axis title
 * from the spec keeps the themed colours. 3D axes (`scene`) get the same
 * treatment; Plotly ignores `scene` unless a trace is 3D.
 */
export function themedLayout(theme: PlotlyTheme, layout: Layout): Layout {
  const axis = { color: theme.ink, gridcolor: GRID, zerolinecolor: ZERO_LINE };
  const scene = (layout.scene as Layout | undefined) ?? {};
  // Plotly draws a grey box behind 3D axes unless told not to.
  const axis3d = { ...axis, showbackground: false };
  return {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    colorway: theme.colorway,
    ...layout,
    font: { ...(layout.font as Layout | undefined), color: theme.ink },
    // The frame owns selection so renderer, values and exports stay aligned.
    legend: { ...(layout.legend as Layout | undefined), itemclick: false, itemdoubleclick: false },
    xaxis: mergeAxis(axis, layout.xaxis),
    yaxis: mergeAxis(axis, layout.yaxis),
    scene: {
      ...scene,
      xaxis: mergeAxis(axis3d, scene.xaxis),
      yaxis: mergeAxis(axis3d, scene.yaxis),
      zaxis: mergeAxis(axis3d, scene.zaxis),
    },
  };
}
