import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportsList } from "@/features/reports/ReportsList";
import { makeReport } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as reportsService from "@/services/reports";

vi.mock("@/services/reports");

describe("ReportsList", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows an empty state when the project has no reports", async () => {
    vi.mocked(reportsService.listReports).mockResolvedValue([]);

    renderWithProviders(<ReportsList projectId="p1" />);

    expect(await screen.findByText("No reports yet.")).toBeInTheDocument();
    expect(reportsService.listReports).toHaveBeenCalledWith("p1");
  });

  it("lists each report with its kind and the matching actions for its status", async () => {
    vi.mocked(reportsService.listReports).mockResolvedValue([
      makeReport({
        id: "a1",
        title: "Build Plan",
        processing_status: "completed",
        metadata: { kind: "build_plan" },
      }),
      makeReport({
        id: "a2",
        processing_status: "failed",
        processing_error: "Report generation failed.",
        metadata: { kind: "study_summary" },
      }),
    ]);

    renderWithProviders(<ReportsList projectId="p1" />);

    expect(await screen.findByText("Build Plan")).toBeInTheDocument();
    expect(screen.getByText(/^Build plan ·/)).toBeInTheDocument();
    expect(screen.getByText(/^Study summary ·/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download PDF" })).toBeInTheDocument();
    expect(screen.getByText("Report generation failed.")).toBeInTheDocument();
  });

  it("retries a failed report and refetches the list", async () => {
    vi.mocked(reportsService.listReports).mockResolvedValue([
      makeReport({ id: "a2", processing_status: "failed", metadata: { kind: "study_summary" } }),
    ]);
    vi.mocked(reportsService.retryReport).mockResolvedValue({ asset_id: "a2", status: "pending" });
    const user = userEvent.setup();

    renderWithProviders(<ReportsList projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Retry" }));

    await waitFor(() => expect(reportsService.retryReport).toHaveBeenCalledWith("a2"));
    await waitFor(() =>
      expect(vi.mocked(reportsService.listReports).mock.calls.length).toBeGreaterThan(1),
    );
  });
});
