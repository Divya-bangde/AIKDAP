import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import mermaid from "mermaid";
import MermaidDiagram from "./MermaidDiagram";

vi.mock("mermaid", () => ({ default: { initialize: vi.fn(), render: vi.fn() } }));
beforeEach(() => { vi.mocked(mermaid.render).mockReset(); vi.mocked(mermaid.initialize).mockReset(); });

describe("readable diagram view", () => {
  it("reports an async render failure, retains raw source and recovers once", async () => {
    vi.mocked(mermaid.render).mockRejectedValueOnce(new Error("parse failed")).mockResolvedValueOnce({ svg: '<svg viewBox="0 0 200 100"><text>Recovered</text></svg>', diagramType: "flowchart-v2" });
    render(<MermaidDiagram source="flowchart LR\nA-->B" />);
    await userEvent.click(await screen.findByRole("button", { name: "Retry diagram renderer" }));
    expect(await screen.findByRole("region", { name: "Scrollable diagram" })).toHaveTextContent("Recovered");
    expect(mermaid.render).toHaveBeenCalledTimes(2);
  });
  it("withholds over-budget diagrams before calling the parser", async () => {
    render(<MermaidDiagram source={"A".repeat(20001)} />);
    expect(await screen.findByRole("status")).toHaveTextContent("rendering budget");
    expect(mermaid.render).not.toHaveBeenCalled();
  });
  it("preserves natural diagram width within a focusable scroll region and exposes zoom controls", async () => {
    vi.mocked(mermaid.render).mockResolvedValue({ svg: '<svg viewBox="0 0 12000 400"><text>Long relationship</text></svg>', diagramType: "flowchart-v2" });
    render(<MermaidDiagram source="flowchart LR\nA-->B" />);
    const region = await screen.findByRole("region", { name: "Scrollable diagram" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.firstElementChild).toHaveStyle({ width: "12000px" });
    expect(mermaid.initialize).toHaveBeenCalledWith(expect.objectContaining({ securityLevel: "strict" }));
    await userEvent.click(screen.getByRole("button", { name: "Zoom in diagram" }));
    expect(region.firstElementChild).toHaveStyle({ width: "15000px" });
    await userEvent.click(screen.getByRole("button", { name: "Reset diagram view" }));
    await waitFor(() => expect(region.firstElementChild).toHaveStyle({ width: "12000px" }));
  });
});
