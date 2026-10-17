import { describe, expect, it } from "vitest";
import { displayLayout } from "./plotly-display";

describe("readable display labels", () => {
  it("abbreviates long categories and axis titles without changing source data or export metadata", () => {
    const title = "A very long physical quantity and unit description ".repeat(4);
    const category = "An intentionally very long category label for a narrow chart";
    const layout = {xaxis:{title:{text:title}},yaxis:{title}};
    const data = [{x:[category],y:[42]}];
    const display = displayLayout(layout,data);
    expect(display.abbreviated).toBe(true);
    expect((display.layout.xaxis as Record<string, unknown>).ticktext).toEqual(["An intentionally very l…"]);
    expect(((display.layout.xaxis as Record<string, unknown>).title as {text:string}).text.length).toBeLessThanOrEqual(32);
    expect(layout.yaxis.title).toBe(title);
    expect(data[0].x).toEqual([category]);
  });
  it("retains short labels, numeric axes, explicit ranges and 3D semantics", () => {
    const layout={xaxis:{type:"linear",range:[0,100]},scene:{zaxis:{title:{text:"Energy (J)"}}}};
    expect(displayLayout(layout,[{x:[1,2],y:[42,10]}])).toMatchObject({abbreviated:false,layout});
  });
});
