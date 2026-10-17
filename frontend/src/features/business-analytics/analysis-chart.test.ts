import { describe, expect, it } from "vitest";
import { chartFigure } from "./analysis-chart";

const result = { columns: ["region", "rev"], rows: [{ region: "West", rev: 150 }, { region: "East", rev: 300 }], total_rows: 2 };

describe("chartFigure", () => {
  it("never coerces exact large-integer strings into rounded chart numbers", () => {
    const exact = {columns:["region","value"],rows:[{region:"West",value:"9007199254740993"}],total_rows:1,numeric_encoding:{value:"exact_integer_string" as const}};
    const chart = {type:"bar" as const,x:"region",y:["value"],series:null};
    expect(() => chartFigure(exact,chart)).toThrow(/exact browser numeric precision/);
    expect(exact.rows[0].value).toBe("9007199254740993");
    expect(() => chartFigure({...exact,chart_withheld_reason:"Exact values remain available in the result table."},chart)).toThrow("Exact values remain available");
    expect(chartFigure(exact,{...chart,type:"table"})).toBeNull();
    expect(chartFigure({...exact,numeric_encoding:undefined,rows:[{region:"West",value:Number.MAX_SAFE_INTEGER}]},chart)?.data[0].y).toEqual([Number.MAX_SAFE_INTEGER]);
  });
  it("maps bar charts to one bar trace per y", () => {
    const figure = chartFigure(result, { type: "bar", x: "region", y: ["rev"], series: null });
    expect(figure?.data).toMatchObject([{ type: "bar", name: "rev", x: ["West", "East"], y: [150, 300] }]);
  });
  it("uses a categorical x-axis for bar charts with numeric categories", () => {
    const numericResult = { columns: ["batch_size", "rev"], rows: [{ batch_size: 32, rev: 150 }, { batch_size: 64, rev: 300 }], total_rows: 2 };
    const figure = chartFigure(numericResult, { type: "bar", x: "batch_size", y: ["rev"], series: null });
    expect(figure?.layout.xaxis).toMatchObject({ type: "category" });
  });
  it("maps pie charts to labels/values", () => {
    const figure = chartFigure(result, { type: "pie", x: "region", y: ["rev"], series: null });
    expect(figure?.data[0]).toMatchObject({ type: "pie", labels: ["West", "East"], values: [150, 300] });
  });
  it("splits a series column into one trace per value", () => {
    const rows = [{ m: "Jan", r: "W", v: 1 }, { m: "Jan", r: "E", v: 2 }, { m: "Feb", r: "W", v: 3 }];
    const figure = chartFigure({ columns: ["m", "r", "v"], rows, total_rows: 3 }, { type: "line", x: "m", y: ["v"], series: "r" });
    expect(figure?.data.map((trace) => trace.name)).toEqual(["v · W", "v · E"]);
    expect(figure?.data[0]).toMatchObject({ type: "scatter", mode: "lines+markers", x: ["Jan", "Feb"], y: [1, 3] });
  });
  it("returns null for table and kpi", () => {
    expect(chartFigure(result, { type: "table", x: null, y: [], series: null })).toBeNull();
    expect(chartFigure(result, { type: "kpi", x: null, y: ["rev"], series: null })).toBeNull();
  });

  const multiple = { columns: ["year", "region", "revenue", "cost"], rows: [
    { year: 2025, region: "West", revenue: 30, cost: 3 }, { year: 2024, region: "East", revenue: 20, cost: 2 },
    { year: 2024, region: "West", revenue: 10, cost: null }, { year: 2025, region: "East", revenue: 40, cost: 4 },
  ], total_rows: 4 };
  const multiChart = { type: "line" as const, x: "year", y: ["revenue", "cost"], series: "region" };

  it.each(["bar", "line"] as const)("retains every metric × series in %s charts", (type) => {
    const figure = chartFigure(multiple, { ...multiChart, type })!;
    expect(figure.data).toHaveLength(4);
    expect(figure.data.map((trace) => trace.name)).toEqual(["revenue · West", "revenue · East", "cost · West", "cost · East"]);
    expect(figure.panels).toHaveLength(2);
    expect(new Set(figure.data.map((trace) => trace.uid)).size).toBe(4);
    expect(figure.data.every((trace) => /^trace-[a-f0-9]+$/.test(String(trace.uid)))).toBe(true);
    const reordered = chartFigure({ ...multiple, rows: [...multiple.rows].reverse() }, { ...multiChart, type })!;
    expect(reordered.data.find((trace) => trace.name === "cost · West")?.uid).toBe(figure.data.find((trace) => trace.name === "cost · West")?.uid);
  });

  it("uses a shared axis only for explicit compatible units", () => {
    expect(chartFigure(multiple, { ...multiChart, metric_units: { revenue: "USD", cost: "kg" } })?.panels).toHaveLength(2);
    expect(chartFigure(multiple, { ...multiChart, metric_units: { revenue: "USD", cost: "USD" } })?.panels).toHaveLength(1);
  });

  it("sorts numeric trends within groups while retaining all metrics and gaps", () => {
    const figure = chartFigure(multiple, { ...multiChart, ordering: { mode: "trend", x_type: "numeric" } })!;
    expect(figure.data[0]).toMatchObject({ x: [2024, 2025], y: [10, 30], connectgaps: false });
    expect(figure.data[2]).toMatchObject({ x: [2024, 2025], y: [null, 3], connectgaps: false });
    expect(chartFigure(multiple, { ...multiChart, ordering: { mode: "trajectory" } })?.data[0].x).toEqual([2025, 2024]);
  });

  it("honors declared month order, date trends and rejects duplicate/invalid dates", () => {
    const monthly = { columns: ["month", "rev"], rows: [{ month: "March", rev: 3 }, { month: "January", rev: 1 }, { month: "February", rev: 2 }], total_rows: 3 };
    expect(chartFigure(monthly, { type: "line", x: "month", y: ["rev"], series: null, ordering: { mode: "ordinal", domain: ["January", "February", "March"] } })?.data[0].y).toEqual([1, 2, 3]);
    const dates = { ...monthly, rows: [{ month: "2025-03-01", rev: 3 }, { month: "2025-01-01", rev: 1 }] };
    const chart = { type: "line" as const, x: "month", y: ["rev"], series: null, ordering: { mode: "trend" as const, x_type: "date" as const } };
    expect(chartFigure(dates, chart)?.data[0].y).toEqual([1, 3]);
    expect(() => chartFigure({ ...dates, rows: [...dates.rows, dates.rows[0]] }, chart)).toThrow(/Duplicate/);
    expect(() => chartFigure({ ...dates, rows: [{ month: "2025-02-30", rev: 1 }] }, chart)).toThrow(/Invalid date/);
  });

  it("rejects missing fields and nonfinite values but retains null values as gaps", () => {
    expect(() => chartFigure(result, { type: "bar", x: "region", y: ["missing"], series: null })).toThrow(/missing/);
    expect(() => chartFigure({ ...result, rows: [{ region: "A", rev: Infinity }] }, { type: "line", x: "region", y: ["rev"], series: null })).toThrow(/nonfinite/);
    expect(chartFigure({ ...result, rows: [{ region: "A", rev: null }, { region: "B", rev: 2 }] }, { type: "line", x: "region", y: ["rev"], series: null })?.data[0]).toMatchObject({ y: [null, 2], connectgaps: false });
    expect(() => chartFigure({ ...result, rows: [{ region: "A", rev: null }] }, { type: "line", x: "region", y: ["rev"], series: null })).toThrow(/every selected metric value is missing/);
  });

  it("does not merge null/string or numeric/string series identities", () => {
    const grouped = { columns: ["x", "group", "v"], rows: [null, "null", 1, "1"].map((group) => ({ x: "A", group, v: 2 })), total_rows: 4 };
    expect(chartFigure(grouped, { type: "bar", x: "x", y: ["v"], series: "group" })?.data).toHaveLength(4);
  });

  it("labels partial pie denominators and rejects invalid or ambiguous pies", () => {
    const chart = { type: "pie" as const, x: "region", y: ["rev"], series: null };
    expect(chartFigure({ ...result, total_rows: 10 }, chart)?.panels[0].title).toBe("Share of displayed categories");
    expect(chartFigure(result, chart)?.notices.join(" ")).toContain("unverified source categories");
    for (const rev of [0, -1, NaN, Infinity, null]) expect(() => chartFigure({ ...result, rows: [{ region: "A", rev }] }, chart)).toThrow();
    expect(() => chartFigure(multiple, { ...multiChart, type: "pie" })).toThrow(/exactly one metric/);
  });
  it("inserts null gaps for absent categories in an explicit ordinal domain", () => {
    const sparse = { columns: ["m", "v"], rows: [{ m: "March", v: 3 }, { m: "January", v: 1 }], total_rows: 2 };
    expect(chartFigure(sparse, { type: "line", x: "m", y: ["v"], series: null, ordering: { mode: "ordinal", domain: ["January", "February", "March"] } })?.data[0]).toMatchObject({ x: ["January", "February", "March"], y: [1, null, 3], connectgaps: false });
  });
  it("enforces trace, per-trace point and label budgets at their boundaries", () => {
    const make = (count: number, grouped = false) => ({ columns: ["x", "v", "s"], rows: Array.from({ length: count }, (_, i) => ({ x: i, v: i, s: grouped ? `group${i}` : "one" })), total_rows: count });
    const chart = { type: "line" as const, x: "x", y: ["v"], series: "s" };
    expect(chartFigure(make(32, true), chart)?.data).toHaveLength(32);
    expect(() => chartFigure(make(33, true), chart)).toThrow(/32 trace/);
    expect(chartFigure(make(10000), chart)?.data[0].x).toHaveLength(10000);
    expect(() => chartFigure(make(10001), chart)).toThrow(/10,000 point/);
    expect(chartFigure({ ...result, rows: [{ region: "a".repeat(500), rev: 1 }] }, { type: "bar", x: "region", y: ["rev"], series: null })).not.toBeNull();
    expect(() => chartFigure({ ...result, rows: [{ region: "a".repeat(501), rev: 1 }] }, { type: "bar", x: "region", y: ["rev"], series: null })).toThrow(/500 character/);
  });
  it("counts inserted ordinal gaps and domain labels against rendering budgets", () => {
    const sparse = { columns: ["x", "v", "s"], rows: Array.from({ length: 32 }, (_, i) => ({ x: 0, v: i, s: `group${i}` })), total_rows: 32 };
    expect(() => chartFigure(sparse, { type: "line", x: "x", y: ["v"], series: "s", ordering: { mode: "ordinal", domain: Array.from({ length: 2000 }, (_, i) => i) } })).toThrow(/50,000 point/);
    expect(() => chartFigure(result, { type: "line", x: "region", y: ["rev"], series: null, ordering: { mode: "ordinal", domain: ["West", "East", "x".repeat(501)] } })).toThrow(/500 character/);
  });
});
