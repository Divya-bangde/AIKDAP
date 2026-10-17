import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssetsLibrary } from "@/features/assets/AssetsLibrary";
import { isSource } from "@/features/assets/asset-state";
import * as assetsService from "@/services/assets";
import * as reportsService from "@/services/reports";
import { makeAsset, makeReport } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/assets");
vi.mock("@/services/reports");
vi.mock("@/lib/export", () => ({ saveBlob: vi.fn() }));

const upload = makeAsset({ id: "doc", title: "paper.pdf" });
const report = makeReport({ id: "rep", title: "Project Synopsis", metadata: { kind: "project_synopsis" } });
const analysis = makeAsset({
  id: "chart",
  title: "Revenue by region",
  asset_type: "chart",
  source: "generated",
  file_size: 0,
});

function renderLibrary(onOpenAnalytics = vi.fn()) {
  vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [report, analysis, upload], next_cursor: null });
  renderWithProviders(<AssetsLibrary projectId="p1" onOpenAnalytics={onOpenAnalytics} />);
  return userEvent.setup();
}

describe("AssetsLibrary", () => {
  beforeEach(() => {
    vi.mocked(reportsService.downloadReport).mockResolvedValue(undefined);
    vi.mocked(assetsService.downloadAssetFile).mockResolvedValue(new Blob(["paper"]));
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists sources and generated outputs together, each with its own action", async () => {
    const onOpenAnalytics = vi.fn();
    const user = renderLibrary(onOpenAnalytics);

    expect(await screen.findByText("paper.pdf")).toBeInTheDocument();
    expect(screen.getByText(/^Reports · Generated ·/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "PDF" }));
    await waitFor(() =>
      expect(reportsService.downloadReport).toHaveBeenCalledWith("rep", "pdf", "Project Synopsis.pdf"),
    );

    await user.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(assetsService.downloadAssetFile).toHaveBeenCalledWith("doc"));

    await user.click(screen.getByRole("button", { name: "Open in Analytics" }));
    expect(onOpenAnalytics).toHaveBeenCalled();
  });

  it("filters by type and by title", async () => {
    const user = renderLibrary();
    await screen.findByText("paper.pdf");

    await user.click(screen.getByRole("button", { name: "Analyses" }));
    expect(screen.getByText("Revenue by region")).toBeInTheDocument();
    expect(screen.queryByText("paper.pdf")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    await user.type(screen.getByLabelText("Search assets"), "synopsis");
    expect(screen.getByText("Project Synopsis")).toBeInTheDocument();
    expect(screen.queryByText("Revenue by region")).not.toBeInTheDocument();
  });

  it("shows an empty state for a project with no assets", async () => {
    vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [], next_cursor: null });
    renderWithProviders(<AssetsLibrary projectId="p1" onOpenAnalytics={vi.fn()} />);

    expect(await screen.findByText("No assets yet.")).toBeInTheDocument();
  });
});

describe("isSource", () => {
  it("treats uploads and imports as sources, generated outputs as not", () => {
    expect(isSource(upload)).toBe(true);
    expect(isSource(makeAsset({ source: "imported" }))).toBe(true);
    expect(isSource(report)).toBe(false);
  });
});
