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
