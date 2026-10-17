import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlotlyVisualization from "./PlotlyVisualization";
import { ChartFrame } from "./ChartFrame";
import { loadPlotly } from "./visualization-registry";

vi.mock("./visualization-registry", async (original) => ({ ...await original<typeof import("./visualization-registry")>(), loadPlotly: vi.fn() }));
vi.mock("@/lib/export", async (original) => ({ ...await original<typeof import("@/lib/export")>(), downloadFile: vi.fn() }));
const runtime = { react: vi.fn(async (_target: HTMLElement) => undefined), purge: vi.fn(), relayout: vi.fn(async (_target: HTMLElement, _update: Record<string, unknown>) => undefined), toImage: vi.fn(async () => "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"><text>42</text></svg>')), Plots: { resize: vi.fn(async () => undefined) } };
let resizeCallback: ResizeObserverCallback;
beforeEach(() => {
  vi.mocked(loadPlotly).mockReset().mockResolvedValue(runtime as unknown as Awaited<ReturnType<typeof loadPlotly>>);
  runtime.react.mockReset().mockResolvedValue(undefined); runtime.purge.mockClear(); runtime.relayout.mockClear(); runtime.toImage.mockClear(); runtime.Plots.resize.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("ResizeObserver", class { constructor(callback: ResizeObserverCallback) { resizeCallback = callback; } observe() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  vi.spyOn(Element.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(420);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(380);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const data = [{ type: "scatter", x: [1, 2], y: [42, 43] }];

describe("guarded Plotly lifecycle", () => {
  it("fits every 3D axis and restores supplied scene ranges on reset", async () => {
    render(<PlotlyVisualization data={[{ type: "scatter3d", x: [1, 2], y: [3, 4], z: [5, 6] }]} layout={{ scene: { xaxis: { range: [0, 1] }, yaxis: { range: [0, 2] }, zaxis: { range: [0, 3] } } }} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Fit chart to data" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Fit chart to data" }));
    expect(Object.keys(runtime.relayout.mock.calls.at(-1)![1]).some((key) => /^(xaxis|yaxis)\./.test(key))).toBe(false);
    expect(runtime.relayout).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({ "scene.xaxis.range": null, "scene.xaxis.autorange": true, "scene.yaxis.range": null, "scene.yaxis.autorange": true, "scene.zaxis.range": null, "scene.zaxis.autorange": true }));
    await userEvent.click(screen.getByRole("button", { name: "Reset chart view" }));
    expect(runtime.relayout).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({ "scene.xaxis.range": [0, 1], "scene.xaxis.autorange": false, "scene.yaxis.range": [0, 2], "scene.yaxis.autorange": false, "scene.zaxis.range": [0, 3], "scene.zaxis.autorange": false }));
  });
  it("refuses an image while the plotted revision is still updating", async () => {
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    runtime.react.mockImplementationOnce(async (target) => { target.classList.add("js-plotly-plot"); await pending; });
    const { container } = render(<ChartFrame title="Revision check" origin="measured" data={data}>{(visible) => <PlotlyVisualization data={visible} layout={{}} />}</ChartFrame>);
    await waitFor(() => expect(runtime.react).toHaveBeenCalledOnce());
    await userEvent.click(screen.getByRole("button", { name: "Download chart image (SVG with context)" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Image export failed");
    expect(runtime.toImage).not.toHaveBeenCalled();
    finish();
    await waitFor(() => expect(container.querySelector<HTMLElement>(".js-plotly-plot")?.dataset.chartRendering).toBe("false"));
    await userEvent.click(screen.getByRole("button", { name: "Download chart image (SVG with context)" }));
    await waitFor(() => expect(runtime.toImage).toHaveBeenCalledOnce());
  });
  it("catches load failures, preserves table and retries only through the bounded action", async () => {
    vi.mocked(loadPlotly).mockRejectedValueOnce(new Error("network unavailable"));
    render(<ChartFrame title="Supported values" origin="measured" data={data}>{(visible) => <PlotlyVisualization data={visible} layout={{}} />}</ChartFrame>);
    expect(await screen.findByRole("status")).toHaveTextContent("could not load or draw");
    await userEvent.click(screen.getByText("Inspect plotted values"));
    expect(await screen.findByRole("table")).toHaveTextContent("42");
    expect(loadPlotly).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Retry chart renderer" }));
    await waitFor(() => expect(runtime.react).toHaveBeenCalledOnce());
    expect(loadPlotly).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByText("Inspect plotted values"));
    expect(loadPlotly).toHaveBeenCalledTimes(2);
  });
  it("contains synchronous resize failures and ignores observers after unmount", async () => {
    const { unmount } = render(<PlotlyVisualization data={data} layout={{}} />);
    await waitFor(() => expect(runtime.react).toHaveBeenCalledOnce());
    runtime.Plots.resize.mockImplementationOnce(() => { throw new Error("hidden div"); });
    resizeCallback([], {} as ResizeObserver);
    expect(await screen.findByRole("status")).toHaveTextContent("could not resize");
    unmount();
    resizeCallback([], {} as ResizeObserver);
    await waitFor(() => expect(runtime.purge).toHaveBeenCalledOnce());
    expect(runtime.Plots.resize).toHaveBeenCalledTimes(1);
  });
  it("catches asynchronous draw failures and disables a second failed retry", async () => {
    runtime.react.mockRejectedValueOnce(new Error("draw failed")).mockRejectedValueOnce(new Error("still failed"));
    render(<PlotlyVisualization data={data} layout={{}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Retry chart renderer" }));
    expect(await screen.findByRole("button", { name: "Renderer retry used; reload the page to try again" })).toBeDisabled();
    expect(runtime.react).toHaveBeenCalledTimes(2);
  });
});
