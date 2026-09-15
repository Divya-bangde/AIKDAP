import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GenerateSynopsisDialog } from "@/features/reports/GenerateSynopsisDialog";
import { makeReport, makeStep } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as reportsService from "@/services/reports";

vi.mock("@/services/reports");

async function startGeneration(kindLabel = "Study summary") {
  const user = userEvent.setup();
  renderWithProviders(<GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />);
  await user.click(screen.getByText(kindLabel));
  await user.click(screen.getByRole("button", { name: /generate/i }));
  return user;
}

describe("GenerateSynopsisDialog", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lets the user choose a kind and starts generation", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(makeReport({ id: "a1", processing_status: "pending" }));

    await startGeneration();

    await waitFor(() =>
      expect(reportsService.generateSynopsis).toHaveBeenCalledWith("p1", "study_summary"),
    );
    expect(await screen.findByText(/generating your report/i)).toBeInTheDocument();
  });

  it("renders the report's pipeline steps as they arrive", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "a1",
        processing_status: "running",
        steps: [
          makeStep(),
          makeStep({ id: "s2", step_index: 1, node_name: "retrieve_evidence", title: "Retrieve section evidence", status: "running", completed_at: null, duration_ms: null }),
        ],
      }),
    );

    await startGeneration();

    expect(await screen.findByText("Gathering your documents")).toBeInTheDocument();
    expect(screen.getByText("Finding evidence for each section")).toBeInTheDocument();
  });

  it("groups the steps by attempt when a report was retried", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "a1",
        processing_status: "running",
        steps: [
          makeStep({ id: "s1", attempt: 1, step_index: 0 }),
          makeStep({ id: "s2", attempt: 1, step_index: 1, node_name: "write_sections", title: "Write report sections", status: "failed", error_message: "Report generation failed (RuntimeError)." }),
          makeStep({ id: "s3", attempt: 2, step_index: 0, status: "running", completed_at: null, duration_ms: null }),
        ],
      }),
    );

    await startGeneration();

    const first = await screen.findByRole("region", { name: "Attempt 1" });
    const second = screen.getByRole("region", { name: "Attempt 2" });
    expect(first).toHaveTextContent("Writing the report");
    expect(second).toHaveTextContent("Gathering your documents");
    expect(second).not.toHaveTextContent("Writing the report");
    expect(screen.getByText("Attempt 2 (latest)")).toBeInTheDocument();
  });

  it("shows no attempt headings for a report run only once", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "a1", processing_status: "running", steps: [makeStep()] }),
    );

    await startGeneration();

    expect(await screen.findByText("Gathering your documents")).toBeInTheDocument();
    expect(screen.queryByText(/^Attempt 1/)).not.toBeInTheDocument();
  });

  it("surfaces a failure with the backend's error message and the failed step", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "a1",
        processing_status: "failed",
        processing_error: "Report generation failed (RuntimeError).",
        steps: [
          makeStep(),
          makeStep({ id: "s2", step_index: 1, node_name: "write_sections", title: "Write report sections", status: "failed", error_message: "Report generation failed (RuntimeError)." }),
        ],
      }),
    );

    await startGeneration("Project synopsis");

    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((alert) => /report generation failed/i.test(alert.textContent ?? ""))).toBe(true);
    expect(screen.getByText("Writing the report")).toBeInTheDocument();
  });

  it("shows a timeout message when generation never finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ delay: null });
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(makeReport({ id: "a1", processing_status: "running" }));

    renderWithProviders(<GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />);
    await user.click(screen.getByText("Study summary"));
    await user.click(screen.getByRole("button", { name: /generate/i }));
    await screen.findByText(/generating your report/i);

    await vi.advanceTimersByTimeAsync(6 * 60 * 1000);

    expect(await screen.findByText(/taking longer than expected/i)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("offers a DOCX/PDF choice once the report is complete", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "a1", title: "Study Summary", processing_status: "completed" }),
    );
    vi.mocked(reportsService.downloadReport).mockResolvedValue(undefined);

    const user = await startGeneration();

    await user.click(await screen.findByRole("button", { name: /download docx/i }));
    expect(reportsService.downloadReport).toHaveBeenCalledWith("a1", "docx", "Study Summary.docx");
    await user.click(screen.getByRole("button", { name: /download pdf/i }));
    expect(reportsService.downloadReport).toHaveBeenCalledWith("a1", "pdf", "Study Summary.pdf");
  });

  it("shows an error when the download itself fails", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "a1", title: "Study Summary", processing_status: "completed" }),
    );
    vi.mocked(reportsService.downloadReport).mockRejectedValue({
      status: 409,
      message: "The report is not ready for download yet.",
    });

    const user = await startGeneration();
    await user.click(await screen.findByRole("button", { name: /download docx/i }));

    expect(await screen.findByText(/not ready for download/i)).toBeInTheDocument();
  });
});
