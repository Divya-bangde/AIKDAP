import { describe, expect, it } from "vitest";
import { contextualSvg, svgFromDataUrl } from "./visualization-export";
import { chartExport } from "./ChartFrame";

describe("contextual SVG export", () => {
  it("keeps required qualifications and source metadata while treating labels as text", () => {
    const context = { title: '<img src=x onerror="alert(1)">', origin: "illustrative", context: ["Mass (kg) by Year", "Filter: year >= 2024"], scope: ["Partial displayed categories"], sources: [{ id: "c1", title: "Source <script>bad</script>", reference: "page 2", onOpen: () => undefined }] };
    const svg = contextualSvg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"><text>42</text></svg>', context, { provenance: "retained" }, { ink: "#fff", background: "#111" });
    const document = new DOMParser().parseFromString(svg, "image/svg+xml");
    expect(document.querySelector("parsererror")).toBeNull();
    expect(document.querySelector("img, script")).toBeNull();
    expect(document.documentElement.textContent).toContain(context.title);
    expect(document.documentElement.textContent).toContain("Partial displayed categories");
    expect(document.querySelector("metadata")?.textContent).toContain("retained");
    expect(document.querySelector("rect")?.getAttribute("fill")).toBe("#111");
  });
  it("maps selected trace provenance back to received data before export", () => {
    const data = [{ type: "bar", uid: "first", x: [2024], y: [1] }, { type: "bar", uid: "second", x: [2024], y: [42] }];
    const exported = chartExport({ title: "Selected proof", origin: "measured", metadata: { provenance: [{ trace: 1, field: "y", point: 0, inputs: [{ value: 42 }] }] } }, [data[1]], {}, undefined, data);
    expect(exported.plotted_trace_mapping[0]).toMatchObject({ exported_trace: 0, received_trace: 1, uid: "second" });
    const proof = exported.metadata.provenance as unknown as { trace: number; point: number; inputs: { value: number }[] }[];
    expect((exported.plotted_traces[proof[0].trace].y as number[])[proof[0].point]).toBe(proof[0].inputs[0].value);
  });
  it("decodes renderer SVG data URLs and rejects unexpected formats", () => {
    expect(svgFromDataUrl("data:image/svg+xml," + encodeURIComponent("<svg/>"))).toBe("<svg/>");
    expect(() => svgFromDataUrl("data:image/png,not-svg")).toThrow(/format/);
  });
});
