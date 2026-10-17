import { describe, expect, it } from "vitest";
import { preserveInteractionView, viewRevision } from "./plotly-view";

describe("semantic plot interaction revision", () => {
  const data = [{ type: "scatter", uid: "measurement", x: [2024], y: [42], line: { color: "blue" } }];
  const layout = { xaxis: { title: { text: "Year" }, type: "linear" }, yaxis: { title: { text: "Mass (kg)" }, range: [0, 100] } };
  it("preserves zoom/camera key when theme and measured values update", () => {
    expect(viewRevision({ ...layout, paper_bgcolor: "black", font: { color: "white" } }, [{ ...data[0], y: [50], line: { color: "red" } }])).toBe(viewRevision(layout, data));
  });
  it("resets on units, declared range/domain, trace identity or explicit Fit changes", () => {
    const key = viewRevision(layout, data);
    expect(viewRevision({ ...layout, yaxis: { title: { text: "Mass (g)" }, range: [0, 100] } }, data)).not.toBe(key);
    expect(viewRevision({ ...layout, yaxis: { ...layout.yaxis, range: [0, 200] } }, data)).not.toBe(key);
    expect(viewRevision({ ...layout, uirevision: "fit-1" }, data)).not.toBe(key);
    expect(viewRevision(layout, [{ ...data[0], uid: "other-source" }])).not.toBe(key);
  });
  it("retains public 3D camera/ranges while applying new theme colors without mutating either input", () => {
    const camera = { eye: { x: 0, y: 0, z: 2.165 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0, y: 0, z: 0 } };
    const current = { scene: { camera, xaxis: { range: [-1, 6], autorange: false } } };
    const next = { scene: { xaxis: { color: "white" } } };
    const result = preserveInteractionView(next, current, "3d") as { scene: { camera: typeof camera; xaxis: { color: string; range: number[] } } };
    expect(result.scene.camera).toEqual(camera);
    expect(result.scene.camera).not.toBe(camera);
    expect(result.scene.xaxis).toMatchObject({ color: "white", range: [-1, 6], autorange: false });
    expect(next.scene.xaxis).toEqual({ color: "white" });
  });
});
