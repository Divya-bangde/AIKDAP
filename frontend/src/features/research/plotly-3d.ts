import scatter3d from "plotly.js/lib/scatter3d";
import surface from "plotly.js/lib/surface";
import Plotly from "./plotly-2d";

Plotly.register([scatter3d, surface] as Parameters<typeof Plotly.register>[0]);
export default Plotly;
