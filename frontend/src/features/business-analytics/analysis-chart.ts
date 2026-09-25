type Row = Record<string, unknown>;
export type AnalysisResultData = { columns: string[]; rows: Row[]; total_rows: number };
export type ChartSpecData = { type: "bar" | "line" | "pie" | "table" | "kpi"; x: string | null; y: string[]; series: string | null };
type Trace = Record<string, unknown> & { name?: string };

/** Plotly traces for an analysis result; `null` when the chart type is
 * rendered as HTML instead (table, kpi). Pure, so it is unit-tested. */
export function chartFigure(result: AnalysisResultData, chart: ChartSpecData): { data: Trace[]; layout: Record<string, unknown> } | null {
  if (chart.type === "table" || chart.type === "kpi" || !chart.x) return null;
  const x = chart.x;
  if (chart.type === "pie") {
    const y = chart.y[0];
    return { data: [{ type: "pie", labels: result.rows.map((row) => row[x]), values: result.rows.map((row) => row[y]) }], layout: {} };
  }
  const base = chart.type === "line" ? { type: "scatter", mode: "lines+markers" } : { type: "bar" };
  const groups = chart.series
    ? [...new Set(result.rows.map((row) => String(row[chart.series!])))].map((value) => ({
        name: value,
        rows: result.rows.filter((row) => String(row[chart.series!]) === value),
      }))
    : null;
  const data: Trace[] = groups
    ? groups.map(({ name, rows }) => ({ ...base, name, x: rows.map((row) => row[x]), y: rows.map((row) => row[chart.y[0]]) }))
    : chart.y.map((y) => ({ ...base, name: y, x: result.rows.map((row) => row[x]), y: result.rows.map((row) => row[y]) }));
  return { data, layout: { xaxis: { title: { text: x } }, barmode: "group", showlegend: data.length > 1 } };
}
