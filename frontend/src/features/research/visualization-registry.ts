export const SUPPORTED_2D = ["bar", "scatter", "pie", "heatmap", "contour"] as const;
export const SUPPORTED_3D = ["scatter3d", "surface"] as const;
export function rendererDimension(data: Record<string, unknown>[]): "2d" | "3d" {
  const ids = data.flatMap((trace) => typeof trace.uid === "string" ? [trace.uid] : []);
  if (ids.some((uid) => !/^[A-Za-z0-9_-]+$/.test(uid)) || new Set(ids).size !== ids.length) throw new Error("Trace identities must be unique and CSS-safe. Inspect the data or regenerate the visualization.");
  for (const trace of data) if (![...SUPPORTED_2D, ...SUPPORTED_3D].includes(trace.type as never)) throw new Error(`Unsupported chart trace: ${String(trace.type)}. Inspect the data or request a supported visualization.`);
  return data.some((trace) => SUPPORTED_3D.includes(trace.type as never)) ? "3d" : "2d";
}
export function loadPlotly(dimension: "2d" | "3d") {
  return dimension === "3d" ? import("./plotly-3d").then((module) => module.default) : import("./plotly-2d").then((module) => module.default);
}
