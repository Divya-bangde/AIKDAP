type Fields = Record<string, unknown>;
/** Preserve interaction state through theme/value updates, while resetting
 * when the meaning, declared domain/ranges, trace identities or Fit token changes. */
export function viewRevision(layout: Fields, data: Fields[]): string {
  const axis = (value: unknown) => {
    const spec = (value && typeof value === "object" ? value : {}) as Fields;
    const title = spec.title;
    return { title: typeof title === "object" && title ? (title as Fields).text : title, type: spec.type, range: spec.range, categoryarray: spec.categoryarray, categoryorder: spec.categoryorder };
  };
  const scene = (layout.scene ?? {}) as Fields;
  return JSON.stringify({ supplied_revision: layout.uirevision, title: typeof layout.title === "object" && layout.title ? (layout.title as Fields).text : layout.title, xaxis: axis(layout.xaxis), yaxis: axis(layout.yaxis), scene: { xaxis: axis(scene.xaxis), yaxis: axis(scene.yaxis), zaxis: axis(scene.zaxis) }, traces: data.map((trace, index) => ({ identity: trace.uid ?? trace.name ?? `anonymous-${index}`, type: trace.type })) });
}

/** Plotly 4's public graphDiv.layout records camera/range interactions, but
 * uirevision alone can reset a 3D camera during React updates. Copy only view
 * fields into the newly themed layout when semantic identity is unchanged. */
export function preserveInteractionView(next: Fields, current: Fields | undefined, dimension: "2d" | "3d"): Fields {
  if (!current) return next;
  const preserveAxes = (fresh: Fields, previous: Fields, names: string[]) => {
    const output = { ...fresh };
    for (const name of names) {
      const oldAxis = (previous[name] ?? {}) as Fields;
      const axis = { ...((fresh[name] ?? {}) as Fields) };
      if (Array.isArray(oldAxis.range)) axis.range = [...oldAxis.range];
      if (typeof oldAxis.autorange === "boolean" || typeof oldAxis.autorange === "string") axis.autorange = oldAxis.autorange;
      output[name] = axis;
    }
    return output;
  };
  if (dimension === "2d") return preserveAxes(next, current, ["xaxis", "yaxis"]);
  const oldScene = (current.scene ?? {}) as Fields;
  const scene = preserveAxes((next.scene ?? {}) as Fields, oldScene, ["xaxis", "yaxis", "zaxis"]);
  for (const key of ["camera", "aspectratio", "aspectmode"]) if (oldScene[key] !== undefined) scene[key] = structuredClone(oldScene[key]);
  return { ...next, scene };
}
