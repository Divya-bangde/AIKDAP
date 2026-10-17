type Fields = Record<string, unknown>;
const shorten = (text: string, length: number) => Array.from(text).length > length ? `${Array.from(text).slice(0, length - 1).join("")}…` : text;

/** Display-only label fitting. Original values and export layout stay intact. */
export function displayLayout(layout: Fields, data: Fields[]) {
  let abbreviated = false;
  const axis = (value: unknown): Fields => {
    const original = (value ?? {}) as Fields;
    const title = original.title;
    const text = typeof title === "string" ? title : title && typeof title === "object" ? (title as Fields).text : null;
    if (typeof text !== "string" || shorten(text, 32) === text) return { ...original };
    abbreviated = true;
    return { ...original, title: typeof title === "string" ? shorten(text, 32) : { ...(title as Fields), text: shorten(text, 32) } };
  };
  const xaxis = { automargin: true, ...axis(layout.xaxis) };
  const yaxis = { automargin: true, ...axis(layout.yaxis) };
  const categories = [...new Set(data.flatMap((trace) => Array.isArray(trace.x) ? trace.x : []).filter((value): value is string => typeof value === "string"))];
  if (categories.some((label) => shorten(label, 24) !== label)) {
    abbreviated = true;
    Object.assign(xaxis, { tickmode: "array", tickvals: categories, ticktext: categories.map((label) => shorten(label, 24)), tickangle: 0 });
  }
  const scene = layout.scene && typeof layout.scene === "object" ? Object.fromEntries(Object.entries(layout.scene as Fields).map(([name, value]) => [name, ["xaxis", "yaxis", "zaxis"].includes(name) ? axis(value) : value])) : undefined;
  return { layout: { ...layout, xaxis, yaxis, ...(scene ? { scene } : {}) }, abbreviated };
}
