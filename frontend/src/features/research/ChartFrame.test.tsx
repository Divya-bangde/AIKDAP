import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChartFrame, chartExport, chartFinding, plottedRows } from "./ChartFrame";
import { downloadFile } from "@/lib/export";

vi.mock("@/lib/export", async (original) => ({ ...await original<typeof import("@/lib/export")>(), downloadFile: vi.fn() }));
beforeEach(() => vi.mocked(downloadFile).mockReset());
const data = [{ type: "scatter", uid: "west", name: "West", x: [2024, 2025], y: [42, null] }, { type: "bar", uid: "east", name: "East", x: [2024], y: [8] }];

describe("ChartFrame", () => {
  it("maps gaps, pies and rectangular grids to their exact plotted coordinates", () => {
    expect(plottedRows(data)).toEqual([{ trace: "West", point: 0, x: 2024, y: 42 }, { trace: "West", point: 1, x: 2025, y: null }, { trace: "East", point: 0, x: 2024, y: 8 }]);
    expect(plottedRows([{ type: "pie", labels: ["A", "B"], values: [60, 40] }])).toMatchObject([{ label: "A", value: 60 }, { label: "B", value: 40 }]);
    expect(plottedRows([{ type: "surface", x: [10, 20], y: [30], z: [[1, null]] }])).toEqual([{ trace: "Trace 1", point: 0, x: 10, y: 30, z: 1 }, { trace: "Trace 1", point: 1, x: 20, y: 30, z: null }]);
  });

  it("describes supported per-trace findings without inventing units", () => {
    expect(chartFinding(data)).toBe("West: 2 values, displayed range 42 to 42, 1 missing (gaps); East: 1 values, displayed range 8 to 8.");
  });

  it("synchronizes selection, renderer, values, export and full-data alternative", async () => {
    const user = userEvent.setup();
    render(<ChartFrame title="Annual observations" origin="measured" data={data} layout={{ xaxis: { title: "Year" }, yaxis: { title: "Mass (kg)" } }} scope={["Partial data; two of ten categories."]} metadata={{ dataset_id: "d1" }}>{(visible) => <p data-testid="renderer">{visible.map((trace) => trace.name).join(", ")}</p>}</ChartFrame>);
    await user.click(screen.getByText("Inspect plotted values"));
    expect(await screen.findByRole("table")).toHaveTextContent("Missing (gap)");
    await user.click(screen.getByText("Select series (2 of 2)"));
    await user.click(screen.getByRole("checkbox", { name: "East" }));
    expect(screen.getByTestId("renderer")).toHaveTextContent("West");
    expect(screen.getByTestId("renderer")).not.toHaveTextContent("East");
    expect(within(screen.getByRole("table")).queryByText("East")).not.toBeInTheDocument();
    expect(screen.getByText("Inspect all received series")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download plotted data and context" }));
    const exported = JSON.parse(vi.mocked(downloadFile).mock.calls[0][1]);
    expect(exported.title).toBe("Annual observations");
    expect(exported.layout.yaxis.title).toBe("Mass (kg)");
    expect(exported.scope).toContain("Series selection: showing 1 of 2 received traces.");
    expect(exported.plotted_traces).toEqual([data[0]]);
    expect(exported.plotted_values[1].y).toBeNull();
    expect(exported.metadata.dataset_id).toBe("d1");
  });

  it("retains source identifiers/context and untrusted titles as inert JSON strings", () => {
    const title = '<img src=x onerror="alert(1)">';
    const exported = JSON.parse(JSON.stringify(chartExport({ title, origin: "illustrative", scope: ["Displayed subset"], context: ["Filter: year >= 2024"], sources: [{ id: "c1", title: "PDF", asset_id: "asset1", chunk_id: "chunk1", reference: "page 2", url: "https://example.test/paper", onOpen: vi.fn() }] }, data)));
    expect(exported.title).toBe(title);
    expect(exported.sources[0]).toMatchObject({ asset_id: "asset1", chunk_id: "chunk1", reference: "page 2" });
    expect(exported.sources[0].onOpen).toBeUndefined();
    expect(exported.origin).toBe("illustrative");
  });

  it("preserves unique named series selection across reorder and uses numeric default grid coordinates", async () => {
    const named = data.map(({ uid: _uid, ...trace }) => trace);
    const view = (traces: typeof named) => <ChartFrame title="Named traces" origin="measured" data={traces}>{(visible) => <p data-testid="renderer">{visible.map((trace) => trace.name).join(", ")}</p>}</ChartFrame>;
    const { rerender } = render(view(named));
    await userEvent.click(screen.getByText("Select series (2 of 2)"));
    await userEvent.click(screen.getByRole("checkbox", { name: "West" }));
    rerender(view([...named].reverse()));
    expect(screen.getByTestId("renderer")).toHaveTextContent("East");
    expect(screen.getByTestId("renderer")).not.toHaveTextContent("West");
    expect(plottedRows([{ type: "heatmap", z: [[4, 5]] }])).toEqual([{ trace: "Trace 1", point: 0, x: 0, y: 0, z: 4 }, { trace: "Trace 1", point: 1, x: 1, y: 0, z: 5 }]);
  });

  it("paginates all values and preserves inspection after export failure", async () => {
    vi.mocked(downloadFile).mockImplementationOnce(() => Promise.reject(new Error("download blocked")) as unknown as void);
    const rows = Array.from({ length: 201 }, (_, index) => index);
    render(<ChartFrame title="Large series" origin="user_entered" data={[{ x: rows, y: rows }]}>{() => <p>Renderer preserved</p>}</ChartFrame>);
    const user = userEvent.setup();
    await user.click(screen.getByText("Inspect plotted values"));
    await waitFor(() => expect(screen.getByText("1–200 of 201 plotted values")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Next values" }));
    expect(screen.getByText("201–201 of 201 plotted values")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Download plotted data and context" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Export failed");
    expect(screen.getByText("Renderer preserved")).toBeInTheDocument();
  });
});
