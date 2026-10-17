import { describe, expect, it } from "vitest";

import { seriesStyle, styledTraces, themedLayout } from "@/features/research/plotly-theme";

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

  it("keeps selection with the shared frame instead of independent legend state", () => {
    expect(themedLayout(theme, {legend:{orientation:"h",itemclick:"toggle"}}).legend).toEqual({orientation:"h",itemclick:false,itemdoubleclick:false});
  });
});

describe("stable non-color series encoding", () => {
  it("preserves identity when filtering or reordering", () => {
    const a = { type: "scatter", name: "Mass · East", uid: "mass-east", x: [1], y: [42] };
    const b = { type: "scatter", name: "Mass · West", uid: "mass-west", x: [1], y: [10] };
    const originals = styledTraces(theme, [a, b]);
    expect(styledTraces(theme, [b, a])).toEqual([originals[1], originals[0]]);
    expect(styledTraces(theme, [a])).toEqual([originals[0]]);
    expect(originals[0]).toMatchObject({ line: { dash: expect.any(String) }, marker: { symbol: expect.any(String) } });
    expect(seriesStyle("mass-east", theme)).not.toEqual(seriesStyle("mass-west", theme));
  });

  it("adds bar/pie patterns and keeps pie categories stable", () => {
    const bar = styledTraces(theme, [{ type: "bar", name: "Mass" }])[0];
    expect(bar).toMatchObject({ marker: { pattern: { shape: expect.any(String) }, line: { color: theme.ink } } });
    const first = styledTraces(theme, [{ type: "pie", labels: ["A", "B"], values: [42, 10] }])[0].marker as { colors: string[]; pattern: { shape: string[] } };
    const reversed = styledTraces(theme, [{ type: "pie", labels: ["B", "A"], values: [10, 42] }])[0].marker;
    expect(reversed).toMatchObject({ colors: [...first.colors].reverse(), pattern: { shape: [...first.pattern.shape].reverse() } });
  });

  it("retains explicit reference styles and uses supported 3D symbols", () => {
    expect(styledTraces(theme,[{type:"scatter",name:"Live curve",line:{color:"#123",width:2.5}}])[0]).toMatchObject({line:{dash:"solid",color:"#123"}});
    expect(styledTraces(theme, [{type: "scatter", name: "Reference", line: {color: "#123",dash:"dot"}}])[0]).toMatchObject({line:{color:"#123",dash:"dot"},marker:{color:"#123"}});
    for (let i = 0; i < 32; i++) {
      const marker = styledTraces(theme, [{type:"scatter3d",name:String(i)}])[0].marker as {symbol:string};
      expect(["circle","square","diamond","cross","x"]).toContain(marker.symbol);
    }
  });
});
