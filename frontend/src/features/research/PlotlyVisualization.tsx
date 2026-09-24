import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

import { themedLayout, usePlotlyTheme } from "@/features/research/plotly-theme";

// Same factory binding as `ExperimentVisualizationChart`. This bundle also
// registers the 3D traces (`scatter3d`, `surface`), so "chart3d" specs
// render through this same component with no extra dependency.
const Plot = createPlotlyComponent(Plotly);

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
  return (
    <Plot
      data={data as Plotly.Data[]}
      layout={themedLayout(theme, {
        autosize: true,
        margin: { l: 48, r: 16, t: 16, b: 48 },
        ...layout,
      })}
      config={{ displaylogo: false, responsive: true }}
      useResizeHandler
      style={{ width: "100%", height: 380 }}
    />
  );
}
