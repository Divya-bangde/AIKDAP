import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

import { themedLayout, usePlotlyTheme } from "@/features/research/plotly-theme";
import type { VisualizationData } from "@/services/experiments";

// `react-plotly.js`'s default export pulls in the full `plotly.js`
// package (~7MB, every trace type and geo/3D support this app never
// uses). The factory + `plotly.js-dist-min` pairing here is the
// documented way to get the same component bound to the smaller,
// still-complete-for-2D-charts bundle instead.
const Plot = createPlotlyComponent(Plotly);

/**
 * Renders raw trace data the backend computed from the plan's actual
 * test cases -- never a server-rendered image, never data this
 * component invents itself (Sprint 16 Phase 6, Part H/R). `note`, when
 * present, describes a trend only; it is never rendered as a causal
 * claim, because the backend never generates one.
 */
export function ExperimentVisualizationChart({ data }: { data: VisualizationData }) {
  const theme = usePlotlyTheme();
  const series = data.series ?? [];
  const hasAnyPoints = series.some((s) => (s.x?.length ?? 0) > 0);

  if (!hasAnyPoints) {
    return (
      <p className="text-sm text-muted-foreground">
        No test cases have both the selected input and output yet -- import test cases to see
        this chart.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Plot
        data={series.map((s) => ({
          x: s.x,
          y: s.y,
          type: s.kind === "bar" ? "bar" : "scatter",
          mode: s.kind === "line" ? "lines+markers" : "markers",
          name: s.name,
        }))}
        layout={themedLayout(theme, {
          autosize: true,
          height: 360,
          margin: { t: 20, r: 20, b: 50, l: 60 },
          xaxis: { title: { text: data.x_label } },
          yaxis: { title: { text: data.y_label } },
        })}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
        useResizeHandler
      />
      {data.note && <p className="text-xs text-muted-foreground">{data.note}</p>}
    </div>
  );
}
