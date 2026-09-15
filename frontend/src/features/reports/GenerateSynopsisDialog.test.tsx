import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GenerateSynopsisDialog } from "@/features/reports/GenerateSynopsisDialog";
import { makeAsset } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as assetsService from "@/services/assets";
import * as reportsService from "@/services/reports";

vi.mock("@/services/assets");
vi.mock("@/services/reports");

describe("GenerateSynopsisDialog", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lets the user choose a kind and starts generation", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(assetsService.getAsset).mockResolvedValue(
      makeAsset({ id: "a1", processing_status: "pending" }),
    );

    renderWithProviders(
      <GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />,
    );

    await user.click(screen.getByText("Study summary"));
    await user.click(screen.getByRole("button", { name: /generate/i }));

    await waitFor(() =>
      expect(reportsService.generateSynopsis).toHaveBeenCalledWith("p1", "study_summary"),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(/generating/i);
  });

  it("surfaces a failure with the backend's error message", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(assetsService.getAsset).mockResolvedValue(
      makeAsset({ id: "a1", processing_status: "failed", processing_error: "Report generation failed (RuntimeError)." }),
    );

    renderWithProviders(
      <GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />,
    );
    await user.click(screen.getByText("Project synopsis"));
    await user.click(screen.getByRole("button", { name: /generate/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/report generation failed/i);
  });

  it("shows a timeout message when generation never finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ delay: null });
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(assetsService.getAsset).mockResolvedValue(makeAsset({ id: "a1", processing_status: "running" }));

    renderWithProviders(
      <GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />,
    );
    await user.click(screen.getByText("Study summary"));
    await user.click(screen.getByRole("button", { name: /generate/i }));
    await screen.findByRole("status");

    await vi.advanceTimersByTimeAsync(6 * 60 * 1000);

    expect(await screen.findByRole("alert")).toHaveTextContent(/taking longer than expected/i);
    vi.useRealTimers();
  });

  it("offers a DOCX/PDF choice once the report is complete", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(assetsService.getAsset).mockResolvedValue(
      makeAsset({ id: "a1", title: "Study Summary", processing_status: "completed" }),
    );
    vi.mocked(reportsService.downloadReport).mockResolvedValue(undefined);

    renderWithProviders(
      <GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />,
    );
    await user.click(screen.getByText("Study summary"));
    await user.click(screen.getByRole("button", { name: /generate/i }));

    const docxButton = await screen.findByRole("button", { name: /download docx/i });
    await user.click(docxButton);
    expect(reportsService.downloadReport).toHaveBeenCalledWith("a1", "docx", "Study Summary.docx");

    await user.click(screen.getByRole("button", { name: /download pdf/i }));
    expect(reportsService.downloadReport).toHaveBeenCalledWith("a1", "pdf", "Study Summary.pdf");
  });

  it("shows an error when the download itself fails", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(assetsService.getAsset).mockResolvedValue(
      makeAsset({ id: "a1", title: "Study Summary", processing_status: "completed" }),
    );
    vi.mocked(reportsService.downloadReport).mockRejectedValue({
      status: 409,
      message: "The report is not ready for download yet.",
    });

    renderWithProviders(
      <GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />,
    );
    await user.click(screen.getByText("Study summary"));
    await user.click(screen.getByRole("button", { name: /generate/i }));

    const docxButton = await screen.findByRole("button", { name: /download docx/i });
    await user.click(docxButton);

    expect(await screen.findByRole("alert")).toHaveTextContent(/not ready for download/i);
  });
});
