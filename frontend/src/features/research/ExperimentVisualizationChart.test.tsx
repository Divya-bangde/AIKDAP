import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ExperimentVisualizationChart } from "./ExperimentVisualizationChart";

vi.mock("./PlotlyVisualization", () => ({ default: ({data}: {data: unknown[]}) => <output aria-label="Rendered test traces">{JSON.stringify(data)}</output> }));

describe("experiment chart frame integration", () => {
  it("keeps deterministic values, labels and the exact table with the visual", async () => {
    render(<ExperimentVisualizationChart data={{chart_type:"line",origin:"expected_test_case_values",plan_id:"fixture-plan",plan_version:3,x_label:"Mass (kg)",y_label:"Energy (J)",note:"Test-case trend; not a causal claim.",series:[{name:"Reference",kind:"line",x:["1","2"],y:[42,84]}]}}/>);
    expect(await screen.findByLabelText("Rendered test traces")).toHaveTextContent('"y":[42,84]');
    expect(screen.getByText("Energy (J) by Mass (kg)")).toBeInTheDocument();
    expect(screen.getByText("Test-case trend; not a causal claim.")).toBeInTheDocument();
    expect(screen.getByText(/expected test-case values \(not observed execution results\)/)).toBeInTheDocument();
    expect(screen.getByText(/fixture-plan; version 3/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Inspect plotted values"));
    expect(await screen.findByRole("cell",{name:/^42$/})).toBeInTheDocument();
    expect(screen.getByRole("button",{name:"Download plotted data and context"})).toBeInTheDocument();
  });

  it("explains an empty test-case result", () => {
    render(<ExperimentVisualizationChart data={{chart_type:"line",origin:"unknown",x_label:"X",y_label:"Y",series:[]}}/>);
    expect(screen.getByText(/No test cases have both/)).toBeInTheDocument();
  });
});
