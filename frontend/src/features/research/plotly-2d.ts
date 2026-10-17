import core from "plotly.js/lib/core";
import bar from "plotly.js/lib/bar";
import pie from "plotly.js/lib/pie";
import heatmap from "plotly.js/lib/heatmap";
import contour from "plotly.js/lib/contour";
import type * as PlotlyType from "plotly.js";

const Plotly = core as unknown as typeof PlotlyType;
// Core already registers scatter. Keep this list aligned with the central registry.
Plotly.register([bar, pie, heatmap, contour] as Parameters<typeof Plotly.register>[0]);
export default Plotly;
