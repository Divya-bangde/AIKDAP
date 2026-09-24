import { describe, expect, it } from "vitest";

import { themedLayout } from "@/features/research/plotly-theme";

const theme = { ink: "#111", colorway: ["#a00", "#0a0"] };

describe("themedLayout", () => {
  it("themes 2D and 3D axes while keeping the spec's own axis settings", () => {
    const layout = themedLayout(theme, {
      xaxis: { title: { text: "Epoch" } },
      scene: { zaxis: { title: { text: "Loss" } } },
    });

    expect(layout.xaxis).toMatchObject({ color: "#111", title: { text: "Epoch" } });
    expect(layout.yaxis).toMatchObject({ color: "#111" });
    const scene = layout.scene as Record<string, Record<string, unknown>>;
    expect(scene.xaxis).toMatchObject({ color: "#111", showbackground: false });
    expect(scene.zaxis).toMatchObject({ color: "#111", title: { text: "Loss" } });
  });

  it("uses the theme palette unless the spec sets its own", () => {
    expect(themedLayout(theme, {}).colorway).toEqual(["#a00", "#0a0"]);
    expect(themedLayout(theme, { colorway: ["#00f"] }).colorway).toEqual(["#00f"]);
  });

  it("keeps text in the theme colour even when the spec sets a font", () => {
    expect(themedLayout(theme, { font: { size: 14, color: "#fff" } }).font).toEqual({ size: 14, color: "#111" });
  });
});
