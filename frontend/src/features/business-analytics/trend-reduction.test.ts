import { describe, expect, it } from "vitest";
import { reduceTrend } from "./trend-reduction";
import { chartFigure, type ChartSpecData } from "./analysis-chart";

describe("bounded trend display reduction", () => {
  it("retains oscillating bucket extrema, endpoints and order without mutating input", () => {
    const values = Array.from({ length: 5000 }, (_, i) => (i % 2 ? -1 : 1) * (i % 17));
    values[877] = -999; values[4222] = 1234;
    const original = [...values];
    const reduced = reduceTrend(values);
    expect(reduced.method).toBe("min_max_contiguous_buckets");
    expect(reduced.indexes.length).toBeLessThanOrEqual(1000);
    expect(reduced.indexes).toContain(0); expect(reduced.indexes).toContain(4999);
    expect(reduced.indexes).toContain(877); expect(reduced.indexes).toContain(4222);
    expect(reduced.indexes).toEqual([...reduced.indexes].sort((a, b) => a - b));
    // Independently check every equal-width bucket's extrema at original indexes.
    for (let start = 0; start < values.length; start += reduced.bucketSize) {
      const bucket = values.slice(start, start + reduced.bucketSize);
      expect(reduced.indexes.some((index) => index >= start && index < start + bucket.length && values[index] === Math.min(...bucket))).toBe(true);
      expect(reduced.indexes.some((index) => index >= start && index < start + bucket.length && values[index] === Math.max(...bucket))).toBe(true);
    }
    expect(values).toEqual(original);
  });

  it("keeps both null-run boundaries and adjacent observations; complex gaps keep the full trace", () => {
    const values: (number | null)[] = Array.from({ length: 5000 }, (_, i) => i);
    for (let i = 1234; i <= 1240; i++) values[i] = null;
    const reduced = reduceTrend(values);
    for (const index of [1233, 1234, 1240, 1241]) expect(reduced.indexes).toContain(index);
    const alternating = Array.from({ length: 2001 }, (_, i) => i % 2 ? null : i);
    const unreduced = reduceTrend(alternating);
    expect(unreduced.method).toBe("full_gap_preservation");
    expect(unreduced.indexes).toHaveLength(2001);
    expect(unreduced.reason).toContain("full accepted trace");
    expect(reduceTrend(Array(2001).fill(1)).indexes).toEqual(reduceTrend(Array(2001).fill(1)).indexes);
  });

  it.each(["numeric", "date"] as const)("reduces only declared %s trends after sorting and maps retained points to actual received rows", (kind) => {
    const rows = Array.from({ length: 3000 }, (_, i) => ({ x: kind === "date" ? new Date(Date.UTC(2020, 0, i + 1)).toISOString().slice(0, 10) : i, value: i >= 1000 && i <= 1005 ? null : i === 1723 ? 9999 : Math.sin(i) })).reverse();
    const original = rows.map((row) => ({ ...row }));
    const figure = chartFigure({ columns: ["x", "value"], rows, total_rows: rows.length }, { type: "line", x: "x", y: ["value"], series: null, ordering: { mode: "trend", x_type: kind } })!;
    const trace = figure.data[0]; const reduction = figure.reductions[0];
    expect(reduction.original_points).toBe(3000);
    expect(reduction.displayed_points).toBeLessThanOrEqual(1000);
    expect(figure.notices.join(" ")).toContain("contiguous-bucket min/max");
    const xs = trace.x as (number | string)[]; const ys = trace.y as (number | null)[];
    reduction.received_result_row_indexes.forEach((rowIndex, point) => { expect(xs[point]).toBe(rows[rowIndex].x); expect(ys[point]).toBe(rows[rowIndex].value); });
    expect(ys).toContain(9999);
    const at = (index: number) => kind === "date" ? new Date(Date.UTC(2020, 0, index + 1)).toISOString().slice(0, 10) : index;
    for (const index of [999, 1000, 1005, 1006]) expect(xs).toContain(at(index));
    expect(xs).toEqual([...xs].sort((a, b) => kind === "date" ? Date.parse(String(a)) - Date.parse(String(b)) : Number(a) - Number(b)));
    expect(rows).toEqual(original);
  });

  it("leaves bars, paths, ordinal and unspecified lines untouched", () => {
    const rows = Array.from({ length: 1500 }, (_, x) => ({ x, value: x }));
    const result = { columns: ["x", "value"], rows, total_rows: rows.length };
    const specs: ChartSpecData[] = [
      { type: "bar", x: "x", y: ["value"], series: null, ordering: { mode: "trend", x_type: "numeric" } },
      { type: "line", x: "x", y: ["value"], series: null, ordering: { mode: "trajectory" } },
      { type: "line", x: "x", y: ["value"], series: null, ordering: { mode: "ordinal", domain: rows.map((row) => row.x) } },
      { type: "line", x: "x", y: ["value"], series: null },
    ];
    for (const spec of specs) { const figure = chartFigure(result, spec)!; expect(figure.data[0].x).toHaveLength(1500); expect(figure.reductions).toEqual([]); }
  });
  it("reduces each group and metric independently without crossing series or losing their extrema", () => {
    const rows = ["West", "East"].flatMap((region) => Array.from({ length: 1600 }, (_, x) => ({ region, x, revenue: x === 77 ? 10000 : x, cost: x === 1001 ? -20000 : -x }))).reverse();
    const figure = chartFigure({ columns: ["region", "x", "revenue", "cost"], rows, total_rows: rows.length }, { type: "line", x: "x", y: ["revenue", "cost"], series: "region", ordering: { mode: "trend", x_type: "numeric" } })!;
    expect(figure.data).toHaveLength(4);
    expect(figure.reductions).toHaveLength(4);
    for (const trace of figure.data) {
      const metric = trace.name!.startsWith("revenue") ? "revenue" : "cost";
      const region = trace.name!.includes("West") ? "West" : "East";
      const reduction = figure.reductions.find((item) => item.uid === trace.uid)!;
      const ys = trace.y as number[];
      expect(ys).toContain(metric === "revenue" ? 10000 : -20000);
      expect(ys.length).toBeLessThanOrEqual(1000);
      reduction.received_result_row_indexes.forEach((rowIndex, point) => { expect(rows[rowIndex].region).toBe(region); expect(rows[rowIndex][metric]).toBe(ys[point]); });
    }
  });
});
