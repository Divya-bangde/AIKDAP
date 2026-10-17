import { describe, expect, it } from "vitest";
import { rendererDimension, SUPPORTED_2D, SUPPORTED_3D } from "./visualization-registry";

describe("supported renderer registry", () => {
  it("retains every supported trace and loads 3D only when requested", () => {
    expect(SUPPORTED_2D).toEqual(["bar", "scatter", "pie", "heatmap", "contour"]);
    for (const type of SUPPORTED_2D) expect(rendererDimension([{ type }])).toBe("2d");
    for (const type of SUPPORTED_3D) expect(rendererDimension([{ type }])).toBe("3d");
    expect(() => rendererDimension([{ type: "radar" }])).toThrow(/Unsupported/);
  });
  it("rejects unsafe or duplicate trace identities before Plotly can create selectors", () => {
    expect(() => rendererDimension([{ type: "bar", uid: '["unsafe"]' }])).toThrow(/CSS-safe/);
    expect(() => rendererDimension([{ type: "bar", uid: "same" }, { type: "scatter", uid: "same" }])).toThrow(/unique/);
  });
});
