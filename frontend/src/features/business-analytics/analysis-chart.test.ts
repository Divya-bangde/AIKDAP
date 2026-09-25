import { describe, expect, it } from "vitest";
import { chartFigure } from "./analysis-chart";

const result = { columns: ["region", "rev"], rows: [{ region: "West", rev: 150 }, { region: "East", rev: 300 }], total_rows: 2 };

describe("chartFigure", () => {
  it("maps bar charts to one bar trace per y", () => {
    const figure = chartFigure(result, { type: "bar", x: "region", y: ["rev"], series: null });
    expect(figure?.data).toEqual([{ type: "bar", name: "rev", x: ["West", "East"], y: [150, 300] }]);
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
    expect(figure?.data.map((trace) => trace.name)).toEqual(["W", "E"]);
    expect(figure?.data[0]).toMatchObject({ type: "scatter", mode: "lines+markers", x: ["Jan", "Feb"], y: [1, 3] });
  });
  it("returns null for table and kpi", () => {
    expect(chartFigure(result, { type: "table", x: null, y: [], series: null })).toBeNull();
    expect(chartFigure(result, { type: "kpi", x: null, y: ["rev"], series: null })).toBeNull();
  });
});
