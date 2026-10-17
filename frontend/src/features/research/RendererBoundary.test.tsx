import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LazyRenderer } from "./RendererBoundary";

const expectedFault = (event: ErrorEvent) => { if (["chunk unavailable", "offline"].includes(event.error?.message)) event.preventDefault(); };
beforeEach(() => window.addEventListener("error", expectedFault));
afterEach(() => { window.removeEventListener("error", expectedFault); vi.restoreAllMocks(); });
describe("local lazy renderer boundary", () => {
  it("retains answer context and retries a rejected lazy module once", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const load = vi.fn(async () => ({ default: ({ value }: { value: string }) => <p>Recovered {value}</p> })).mockRejectedValueOnce(new Error("chunk unavailable"));
    render(<><p>Supported answer retained</p><LazyRenderer label="chart" load={load} rendererProps={{ value: "42" }} /></>);
    const button = await screen.findByRole("button", { name: "Retry chart renderer" });
    expect(screen.getByText("Supported answer retained")).toBeInTheDocument();
    await userEvent.click(button);
    expect(await screen.findByText("Recovered 42")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });
  it("bounds repeated load failures", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const load = vi.fn(async () => ({ default: () => <p>Unused</p> })).mockRejectedValue(new Error("offline"));
    render(<LazyRenderer label="diagram" load={load} rendererProps={{}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Retry diagram renderer" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Renderer retry used; reload the page to try again" })).toBeDisabled());
    expect(load).toHaveBeenCalledTimes(2);
  });
});
