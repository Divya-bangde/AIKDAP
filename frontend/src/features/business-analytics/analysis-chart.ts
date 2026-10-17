import { reduceTrend } from "./trend-reduction";

type Row = Record<string, unknown>;
type Category = string | number;
export type AnalysisResultData = { columns: string[]; rows: Row[]; total_rows: number; scope?: { partial: boolean; dataset_truncated: boolean; label: string; description: string }; numeric_encoding?: Record<string, "exact_integer_string">; chart_withheld_reason?: string };
export type ChartSpecData = {
  type: "bar" | "line" | "pie" | "table" | "kpi";
  x: string | null;
  y: string[];
  series: string | null;
  metric_units?: Record<string, string>;
  ordering?: { mode: "trend" | "ordinal" | "trajectory"; x_type?: "numeric" | "date"; domain?: Category[] } | null;
};
type Trace = Record<string, unknown> & { name?: string };
export type ChartPanel = { id: string; title: string; data: Trace[]; layout: Record<string, unknown> };
export type TraceReduction = { uid: string; name: string; original_points: number; displayed_points: number; method: string; bucket_size: number; retained_ordered_point_indexes: number[]; received_result_row_indexes: number[]; reason?: string };
export type ChartFigure = { data: Trace[]; layout: Record<string, unknown>; panels: ChartPanel[]; notices: string[]; reductions: TraceReduction[] };
export class ChartDataError extends Error {}
const fail = (message: string): never => { throw new ChartDataError(message); };
const identity = (value: unknown) => JSON.stringify([typeof value, value]);
// Plotly embeds uid in CSS selectors during purge. Encode UTF-16 units without
// punctuation, preserving collision-free identity even for untrusted labels.
const traceUid = (value: unknown) => `trace-${identity(value).split("").map((char) => char.charCodeAt(0).toString(16).padStart(4, "0")).join("")}`;
const isCategory = (value: unknown): value is Category => typeof value === "string" || (typeof value === "number" && Number.isFinite(value));
const isValue = (value: unknown) => value === null || (typeof value === "number" && Number.isFinite(value));

function orderedRows(rows: Row[], chart: ChartSpecData): Row[] {
  const order = chart.ordering;
  if (chart.type !== "line" || !order || order.mode === "trajectory") return rows;
  const x = chart.x!;
  let coordinate: (row: Row) => number;
  if (order.mode === "ordinal") {
    const domain = order.domain;
    if (!domain?.length || new Set(domain.map(identity)).size !== domain.length) fail("Ordinal lines need a unique, explicit domain order (for example January through December).");
    coordinate = (row) => {
      const index = domain!.findIndex((value) => identity(value) === identity(row[x]));
      if (index < 0) fail(`The ${x} value ${String(row[x])} is outside the declared domain.`);
      return index;
    };
  } else if (order.x_type === "numeric") {
    coordinate = (row) => typeof row[x] === "number" && Number.isFinite(row[x]) ? row[x] as number : fail(`Trend axis ${x} requires finite numeric values; missing coordinates are not connected.`);
  } else if (order.x_type === "date") {
    coordinate = (row) => {
      const value = row[x];
      if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value)) return fail(`Trend axis ${x} requires ISO dates.`);
      const time = Date.parse(value);
      const calendar = value.slice(0, 10);
      const calendarTime = Date.parse(`${calendar}T00:00:00Z`);
      if (!Number.isFinite(time) || !Number.isFinite(calendarTime) || new Date(calendarTime).toISOString().slice(0, 10) !== calendar) return fail(`Invalid date on ${x}: ${value}.`);
      return time;
    };
  } else return fail("Trend lines need an explicit numeric or date axis policy.");
  const indexed = rows.map((row) => ({ row, value: coordinate(row) }));
  if (new Set(indexed.map(({ value }) => value)).size !== indexed.length) fail(`Duplicate ${x} coordinates need an explicit aggregation or trajectory policy; no values were combined.`);
  if (order.mode === "ordinal") {
    const byCoordinate = new Map(indexed.map(({ row, value }) => [value, row]));
    return order.domain!.map((value, index) => byCoordinate.get(index) ?? Object.fromEntries([[x, value], ...chart.y.map((metric) => [metric, null])]));
  }
  return indexed.sort((a, b) => a.value - b.value).map(({ row }) => row);
}

/** Preserve every (series, metric) pair. Unknown/incompatible units use
 * separate panels; values are never coerced or silently discarded. */
export function chartFigure(result: AnalysisResultData, chart: ChartSpecData): ChartFigure | null {
  if (chart.type === "table" || chart.type === "kpi") return null;
  if (result.chart_withheld_reason) fail(result.chart_withheld_reason);
  if (!chart.x || !chart.y.length) return fail("A chart needs an x field and at least one metric. View the result table or choose chart fields.");
  const x = chart.x;
  if (new Set(chart.y).size !== chart.y.length) fail("Selected metrics must be unique.");
  const fields = [x, ...chart.y, ...(chart.series ? [chart.series] : [])];
  if (fields.some((field) => result.numeric_encoding?.[field] === "exact_integer_string")) fail("Chart withheld because selected integers exceed exact browser numeric precision. Inspect the exact values in the result table.");
  for (const field of fields) {
    if (!result.columns.includes(field) || result.rows.some((row) => !Object.prototype.hasOwnProperty.call(row, field))) fail(`Chart field ${field} is missing from the result. View the available columns in the table.`);
  }
  const labels = [...fields, ...Object.values(chart.metric_units ?? {}), ...(chart.ordering?.domain ?? []).filter((value): value is string => typeof value === "string"), ...result.rows.flatMap((row) => fields.map((field) => row[field])).filter((value): value is string => typeof value === "string")];
  if (labels.some((label) => label.length > 500)) fail("Chart labels exceed the 500 character budget. Shorten labels before plotting.");
  const serialized = JSON.stringify({ chart, rows: result.rows.map((row) => fields.map((field) => row[field])) });
  if (new TextEncoder().encode(serialized).byteLength > 2000000) fail("Chart data exceeds the 2,000,000 serialized byte budget. Filter or aggregate the result.");
  if (!result.rows.length) fail("Not enough data to draw the requested chart. The result contains no rows.");
  if (result.rows.length * chart.y.length > 50000) fail("This chart exceeds the 50,000 point budget. Filter or aggregate the result before plotting.");
  if (result.rows.some((row) => !isCategory(row[x]))) fail(`Chart axis ${x} contains missing or invalid categories.`);
  for (const metric of chart.y) if (result.rows.some((row) => !isValue(row[metric]))) fail(`Metric ${metric} contains a nonnumeric or nonfinite value. Values were not coerced.`);
  if (chart.y.every((metric) => result.rows.every((row) => row[metric] === null))) fail("Not enough chart data: every selected metric value is missing. View the result table.");
  const notices: string[] = [];
  const reductions: TraceReduction[] = [];
  for (const metric of chart.y) if (result.rows.every((row) => row[metric] === null)) notices.push(`${metric}: no observed values; its trace contains only missing-value gaps.`);
  const partial = result.total_rows > result.rows.length;
  if (partial) notices.push(`Partial data: showing ${result.rows.length} of ${result.total_rows} result rows.`);
  if (result.scope) notices.push(`${result.scope.partial ? "Partial data: " : ""}${result.scope.label}. ${result.scope.description}`);
  if (chart.type === "pie") {
    if (chart.y.length !== 1 || chart.series) fail("A pie requires exactly one metric and no series grouping. Choose separate pies or a bar chart to retain every field.");
    if (result.rows.length > 50) fail("A pie supports at most 50 categories. Aggregate categories before plotting.");
    const metric = chart.y[0];
    const values = result.rows.map((row) => row[metric]);
    if (values.some((value) => typeof value !== "number" || !Number.isFinite(value) || value < 0)) fail("Pie values must be finite, nonnegative numbers without missing values.");
    const total = (values as number[]).reduce((sum, value) => sum + value, 0);
    if (!Number.isFinite(total) || total <= 0) fail("A pie needs a finite positive total.");
    if (new Set(result.rows.map((row) => identity(row[x]))).size !== result.rows.length) fail("Pie categories must be uniquely aggregated before plotting.");
    const title = "Share of displayed categories";
    notices.push(partial || !result.scope || result.scope.partial ? "Percentages use only displayed values; omitted or unverified source categories are excluded. No Other value has been inferred." : "All result categories are displayed. Percentages describe this filtered computation, not the original dataset.");
    const data: Trace[] = [{ type: "pie", uid: traceUid(["pie", metric]), name: metric, labels: result.rows.map((row) => row[x]), values, sort: false }];
    const layout = { title: { text: title }, showlegend: true };
    return { data, layout, panels: [{ id: metric, title, data, layout }], notices, reductions };
  }
  if (chart.type === "line") notices.push(chart.ordering?.mode === "trend" ? "Trend: coordinates sorted within each series; duplicate coordinates rejected; missing values remain gaps." : chart.ordering?.mode === "ordinal" ? "Ordinal line: declared domain order; missing values remain gaps." : chart.ordering?.mode === "trajectory" ? "Trajectory: supplied point sequence retained; missing values remain gaps." : "Ordering unspecified: supplied point sequence retained. A trend requires a declared numeric, date, or ordinal order.");
  const groups = new Map<string, { value: unknown; rows: Row[] }>();
  for (const row of result.rows) {
    const value = chart.series ? row[chart.series] : undefined;
    const key = identity(value);
    if (!groups.has(key)) groups.set(key, { value, rows: [] });
    groups.get(key)!.rows.push(row);
  }
  if (groups.size * chart.y.length > 32) fail("This chart exceeds the 32 trace budget. Filter series or aggregate groups; the result table remains available.");
  if ([...groups.values()].some((group) => Math.max(group.rows.length, chart.ordering?.domain?.length ?? 0) > 10000)) fail("A trace exceeds the 10,000 point budget. Filter or aggregate before plotting.");
  const projectedPoints = [...groups.values()].reduce((sum, group) => sum + Math.max(group.rows.length, chart.ordering?.mode === "ordinal" ? chart.ordering.domain?.length ?? 0 : 0) * chart.y.length, 0);
  if (projectedPoints > 50000) fail("This chart exceeds the 50,000 point budget after inserting ordinal gaps. Filter or aggregate before plotting.");
  const base = chart.type === "line" ? { type: "scatter", mode: "lines+markers", connectgaps: false } : { type: "bar" };
  const xaxis = { ...(chart.type === "bar" ? { type: "category" } : {}), title: { text: x } };
  const metrics = chart.y;
  const units = metrics.map((metric) => chart.metric_units?.[metric]?.trim());
  const sharedUnits = metrics.length === 1 || (units.every(Boolean) && new Set(units).size === 1);
  if (metrics.length > 1 && !sharedUnits) notices.push("Metrics use separate panels because units differ or are unspecified. No unit conversion is assumed.");
  const sourceIndexes = new Map(result.rows.map((row, index) => [row, index]));
  const traces = (selected: string[]) => selected.flatMap((metric) => [...groups.entries()].map(([key, group]) => {
    const rows = orderedRows(group.rows, chart);
    const groupLabel = chart.series ? `${String(group.value)}${typeof group.value === "string" ? "" : ` (${typeof group.value})`}` : "";
    const uid = traceUid([metric, key]);
    const name = groupLabel ? `${metric} · ${groupLabel}` : metric;
    let displayed = rows;
    if (chart.type === "line" && chart.ordering?.mode === "trend" && rows.length > 1000) {
      const reduced = reduceTrend(rows.map((row) => row[metric] as number | null));
      displayed = reduced.indexes.map((index) => rows[index]);
      reductions.push({ uid, name, original_points: rows.length, displayed_points: displayed.length, method: reduced.method, bucket_size: reduced.bucketSize, retained_ordered_point_indexes: reduced.indexes, received_result_row_indexes: displayed.map((row) => sourceIndexes.get(row)!), ...(reduced.reason ? { reason: reduced.reason } : {}) });
      notices.push(reduced.reason ? `${name}: ${displayed.length} of ${rows.length} points displayed. ${reduced.reason}` : `${name}: display reduction shows ${displayed.length} of ${rows.length} original points using contiguous-bucket min/max sampling. First/last points, global extrema and null-gap boundaries are retained. Full received values remain in the analysis result table and download.`);
    }
    return { ...base, uid, name, x: displayed.map((row) => row[x]), y: displayed.map((row) => row[metric]) };
  }));
  const panels = (sharedUnits ? [metrics] : metrics.map((metric) => [metric])).map((selected) => {
    const unit = chart.metric_units?.[selected[0]]?.trim();
    const title = `${selected.join(", ")}${unit ? ` (${unit})` : " (units unspecified)"}`;
    const data = traces(selected);
    return { id: selected.join("\u0000"), title, data, layout: { xaxis, yaxis: { title: { text: title } }, barmode: "group", showlegend: data.length > 1 } };
  });
  return { data: panels.flatMap((panel) => panel.data), layout: panels[0].layout, panels, notices, reductions };
}
