import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KaggleImportDialog } from "@/features/business-analytics/KaggleImportDialog";
import { renderWithProviders } from "@/test/render";
import * as analyticsService from "@/services/analytics";

vi.mock("@/services/analytics");

function render(onImported = vi.fn()) {
  return renderWithProviders(
    <KaggleImportDialog open onOpenChange={vi.fn()} projectId="p1" onImported={onImported} />,
  );
}

describe("KaggleImportDialog", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("lists files after searching a dataset reference", async () => {
    vi.mocked(analyticsService.listKaggleFiles).mockResolvedValue([
      { name: "data.csv", size: 2048 },
    ]);
    const user = userEvent.setup();
    render();

    await user.type(screen.getByPlaceholderText("owner/dataset"), "someone/dataset");
    await user.click(screen.getByRole("button", { name: /find files/i }));

    expect(await screen.findByText("data.csv")).toBeInTheDocument();
    expect(analyticsService.listKaggleFiles).toHaveBeenCalledWith("someone", "dataset");
  });

  it("imports the chosen file and calls onImported", async () => {
    vi.mocked(analyticsService.listKaggleFiles).mockResolvedValue([
      { name: "data.csv", size: 2048 },
    ]);
    vi.mocked(analyticsService.importKaggle).mockResolvedValue({ status: "queued" });
    const onImported = vi.fn();
    const user = userEvent.setup();
    render(onImported);

    await user.type(screen.getByPlaceholderText("owner/dataset"), "someone/dataset");
    await user.click(screen.getByRole("button", { name: /find files/i }));
    await user.click(await screen.findByRole("radio", { name: /data\.csv/i }));
    await user.click(screen.getByRole("button", { name: /^import$/i }));

    await waitFor(() =>
      expect(analyticsService.importKaggle).toHaveBeenCalledWith("p1", "someone", "dataset", "data.csv"),
    );
    expect(await screen.findByText(/import started/i)).toBeInTheDocument();
    expect(onImported).toHaveBeenCalled();
  });

  it("shows a not-configured message on a 503", async () => {
    vi.mocked(analyticsService.listKaggleFiles).mockRejectedValue({
      status: 503,
      message: "Service unavailable",
    });
    const user = userEvent.setup();
    render();

    await user.type(screen.getByPlaceholderText("owner/dataset"), "someone/dataset");
    await user.click(screen.getByRole("button", { name: /find files/i }));

    expect(await screen.findByText(/kaggle import isn't configured/i)).toBeInTheDocument();
  });
});
