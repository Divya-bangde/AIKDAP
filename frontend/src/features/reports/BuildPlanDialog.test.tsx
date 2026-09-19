import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BuildPlanDialog } from "@/features/reports/BuildPlanDialog";
import { aiProfile, makeAsset, makeReport, makeStep } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as reportsService from "@/services/reports";

vi.mock("@/services/reports");

const processedOne = makeAsset({ id: "a1", title: "First paper.pdf" });
const processedTwo = makeAsset({ id: "a2", title: "Second paper.pdf" });
const unprocessed = makeAsset({
  id: "a3",
  title: "Still processing.pdf",
  processing_status: "running",
  ai_profile: aiProfile({ status: "pending", embedding_status: "pending" }),
});

function render(assets = [processedOne, processedTwo, unprocessed]) {
  return renderWithProviders(
    <BuildPlanDialog open onOpenChange={vi.fn()} projectId="p1" assets={assets} />,
  );
}

describe("BuildPlanDialog — paper picker", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("lists only processed documents, all ticked by default", () => {
    render();

    const first = screen.getByRole("checkbox", { name: /first paper\.pdf/i });
    const second = screen.getByRole("checkbox", { name: /second paper\.pdf/i });
    expect(first).toBeChecked();
    expect(second).toBeChecked();
    // An unprocessed document cannot be built from, so it is not offered.
    expect(screen.queryByRole("checkbox", { name: /still processing/i })).not.toBeInTheDocument();
  });

  it("sends every ticked paper and starts the run", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "r1", title: "Build Plan", processing_status: "running" }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    await waitFor(() =>
      expect(reportsService.generateBuildPlan).toHaveBeenCalledWith("p1", ["a1", "a2"]),
    );
  });

  it("sends only the papers still ticked after one is unticked", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "r1", processing_status: "running" }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("checkbox", { name: /first paper\.pdf/i }));
    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    await waitFor(() =>
      expect(reportsService.generateBuildPlan).toHaveBeenCalledWith("p1", ["a2"]),
    );
  });

  it("blocks an empty selection", async () => {
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("checkbox", { name: /first paper\.pdf/i }));
    await user.click(screen.getByRole("checkbox", { name: /second paper\.pdf/i }));

    expect(screen.getByRole("button", { name: /get build plan/i })).toBeDisabled();
    expect(reportsService.generateBuildPlan).not.toHaveBeenCalled();
  });

  it("explains itself when the project has no processed documents", () => {
    render([unprocessed]);

    expect(screen.getByText(/no processed documents/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /get build plan/i })).toBeDisabled();
  });
});

describe("BuildPlanDialog — run states", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("shows progress and the pipeline while running", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "r1",
        processing_status: "running",
        steps: [makeStep({ node_name: "extract", title: "Extract what the paper builds" })],
      }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/generating your report/i);
    expect(await screen.findByText("Extract what the paper builds")).toBeInTheDocument();
  });

  it("offers a retry when generation failed, and re-runs it", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "r1",
        processing_status: "failed",
        processing_error: "Report generation failed (RuntimeError).",
      }),
    );
    vi.mocked(reportsService.retryReport).mockResolvedValue({ asset_id: "r1", status: "pending" });
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/RuntimeError/);
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(reportsService.retryReport).toHaveBeenCalledWith("r1"));
  });

  it("offers both downloads once complete", async () => {
    vi.mocked(reportsService.generateBuildPlan).mockResolvedValue({
      asset_id: "r1",
      status: "pending",
    });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "r1", title: "Build Plan", processing_status: "completed" }),
    );
    const user = userEvent.setup();
    render();

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(await screen.findByRole("button", { name: "Download DOCX" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download PDF" })).toBeInTheDocument();
  });
});
