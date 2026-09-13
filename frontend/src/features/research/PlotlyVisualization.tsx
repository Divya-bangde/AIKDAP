import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

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
  // Plotly needs concrete colors, not CSS variables: take the page's text
  // color so axes stay readable in both light and dark themes.
  const ink = getComputedStyle(document.body).color;
  const axis = { color: ink, gridcolor: "rgba(128, 128, 128, 0.2)", zerolinecolor: "rgba(128, 128, 128, 0.35)" };
  return (
    <Plot
      data={data as Plotly.Data[]}
      layout={{
        autosize: true,
        margin: { l: 48, r: 16, t: 16, b: 48 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: ink },
        ...layout,
        // Merged, not replaced: a spec's own axis settings (titles) keep these colors.
        xaxis: { ...axis, ...(layout.xaxis as object | undefined) },
        yaxis: { ...axis, ...(layout.yaxis as object | undefined) },
      }}
      config={{ displaylogo: false, responsive: true }}
      useResizeHandler
      style={{ width: "100%", height: 380 }}
    />
  );
}
