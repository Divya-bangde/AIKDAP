import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnalysisCard } from "@/features/business-analytics/AnalysisCard";
import { renderWithProviders } from "@/test/render";
import type { AnalysisRead } from "@/services/analytics";

vi.mock("@/features/research/PlotlyVisualization", () => ({
  default: () => <div data-testid="plot" />,
}));
vi.mock("@/features/workflow-timeline/WorkflowTimeline", () => ({
  WorkflowTimeline: () => <div data-testid="workflow-timeline" />,
}));

function makeAnalysis(overrides: Partial<AnalysisRead> = {}): AnalysisRead {
  return {
    id: "an1",
    dataset_id: "d1",
    question: "What is total revenue by region?",
    status: "completed",
    error: null,
    plan: {
      unanswerable_reason: null,
      filters: [],
      time_bucket: null,
      group_by: ["region"],
      metrics: [{ column: "revenue", agg: "sum", alias: "total", as_share: false }],
      sort: null,
      limit: null,
      chart: { type: "bar", x: "region", y: ["total"], series: null },
    },
    result: {
      columns: ["region", "total"],
      rows: [{ region: "West", total: 150 }],
      total_rows: 1,
    },
    narrative: "West had 150 in revenue.",
    unverified_numbers: [],
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("AnalysisCard", () => {
  it("renders a plot and narrative for a completed bar analysis", async () => {
    renderWithProviders(<AnalysisCard analysis={makeAnalysis()} />);

    expect(await screen.findByTestId("plot")).toBeInTheDocument();
    expect(screen.getByText("West had 150 in revenue.")).toBeInTheDocument();
  });

  it("wraps an unverified number in mark", () => {
    renderWithProviders(
      <AnalysisCard
        analysis={makeAnalysis({ narrative: "Revenue grew by 42%.", unverified_numbers: ["42%"] })}
      />,
    );

    const mark = screen.getByTitle("Not found in the result");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveTextContent("42%");
  });

  it("marks the longer token when a short unverified number is a substring of another", () => {
    renderWithProviders(
      <AnalysisCard
        analysis={makeAnalysis({ narrative: "grew 12% to 12", unverified_numbers: ["12", "12%"] })}
      />,
    );

    const marks = screen.getAllByTitle("Not found in the result");
    expect(marks).toHaveLength(2);
    expect(marks[0]).toHaveTextContent("12%");
    expect(marks[1].textContent).toBe("12");
  });

  it("shows the error message when failed", () => {
    renderWithProviders(
      <AnalysisCard analysis={makeAnalysis({ status: "failed", error: "Could not parse question." })} />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Could not parse question.");
  });

  it("shows narrative with no plot when unanswerable", () => {
    renderWithProviders(
      <AnalysisCard
        analysis={makeAnalysis({
          result: null,
          narrative: "This question can't be answered from the dataset.",
        })}
      />,
    );

    expect(screen.getByText("This question can't be answered from the dataset.")).toBeInTheDocument();
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
  });

  it("renders a big number for a kpi analysis", () => {
    renderWithProviders(
      <AnalysisCard
        analysis={makeAnalysis({
          plan: {
            unanswerable_reason: null,
            filters: [],
            time_bucket: null,
            group_by: [],
            metrics: [{ column: "revenue", agg: "sum", alias: "total", as_share: false }],
            sort: null,
            limit: null,
            chart: { type: "kpi", x: null, y: ["total"], series: null },
          },
          result: { columns: ["total"], rows: [{ total: 4200 }], total_rows: 1 },
        })}
      />,
    );

    expect(screen.getByText("4200", { selector: "p.text-3xl" })).toBeInTheDocument();
  });

  it("lists metrics under How this was computed", () => {
    renderWithProviders(<AnalysisCard analysis={makeAnalysis()} />);

    expect(screen.getByText(/sum\(revenue\) as total/)).toBeInTheDocument();
  });
});
