import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResearchVisualization } from "./AnswerVisualization";

vi.mock("@/features/research/PlotlyVisualization", () => ({ default: () => <div data-testid="plot" /> }));
vi.mock("@/features/research/MermaidDiagram", () => ({ default: () => <div data-testid="diagram" /> }));

describe("visual completion", () => {
  it("explains missing requested output separately from a successful answer", () => {
    render(<ResearchVisualization outcome={{ status: "not_enough_data", reason: "Only one year is supported.", next_action: "Add another year of observations.", verification: "unverified", requested: { type: "bar", axis_semantics: "category" } }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Visualization not enough data");
    expect(screen.getByText("Only one year is supported.")).toBeInTheDocument();
    expect(screen.getByText(/Requested visual: bar/)).toBeInTheDocument();
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
  });
  it("withholds a legacy chart with honest unverified status", () => {
    render(<ResearchVisualization spec={{ kind: "chart2d", data: [{ type: "bar", x: [2024], y: [999999] }] }} />);
    expect(screen.getByRole("status")).toHaveTextContent("unverified");
    expect(screen.getByText(/no independent value-verification metadata/)).toBeInTheDocument();
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
  });
  it("renders a produced verified visual without changing bar intent", async () => {
    render(<ResearchVisualization spec={{ kind: "chart2d", origin: "measured", title: "Annual counts", data: [{ type: "bar", x: [2024], y: [42] }] }} outcome={{ status: "produced", reason: "Values checked against source.", verification: "verified" }} />);
    expect(await screen.findByTestId("plot")).toBeInTheDocument();
    expect(screen.getByText("Data origin: measured.")).toBeInTheDocument();
  });
  it("renders produced diagrams where numeric point verification does not apply", async () => {
    render(<ResearchVisualization spec={{ kind: "diagram", title: "Evidence relationship", mermaid: "flowchart LR\nA-->B" }} outcome={{ status: "produced", reason: "Diagram structure validated; numeric verification does not apply.", verification: "unverified" }} />);
    expect(await screen.findByTestId("diagram")).toBeInTheDocument();
  });
  it("opens only resolved evidence referenced by the plotted values", async () => {
    const open = vi.fn();
    render(<ResearchVisualization spec={{ kind: "chart2d", provenance: [{ trace: 0, field: "y", point: 0, formula: "identity", unit: "kg", inputs: [{ citation_id: "c1", reference: "chunk1", value: 42, unit: "kg" }] }] }} outcome={{ status: "produced", reason: "Verified", verification: "verified" }} evidence={[{ id: "c1", title: "Authorized measurement", onOpen: open }, { id: "foreign", title: "Unrelated source", onOpen: vi.fn() }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Open chart evidence: Authorized measurement" }));
    expect(open).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Unrelated source/)).not.toBeInTheDocument();
  });
});
