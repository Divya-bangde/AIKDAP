import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentCard } from "@/features/assets/DocumentCard";
import * as assetsService from "@/services/assets";
import * as reportsService from "@/services/reports";
import { aiProfile } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

vi.mock("@/services/assets");
vi.mock("@/services/reports");

type AssetRead = components["schemas"]["AssetRead"];

function makeAsset(overrides: Partial<AssetRead> = {}): AssetRead {
  return {
    id: "asset-1",
    project_id: "project-1",
    owner_id: "owner-1",
    title: "doc",
    description: null,
    asset_type: "other",
    status: "active",
    mime_type: "text/plain",
    file_name: "abc_poultry.txt",
    file_extension: ".txt",
    file_size: 1024,
    checksum: "abc",
    source: "upload",
    version: 1,
    tags: [],
    metadata: {},
    ai_profile: aiProfile({ embedding_status: "completed", status: "completed" }),
    created_by: null,
    processing_status: "completed",
    processing_error: null,
    processing_started_at: null,
    processing_completed_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("DocumentCard", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("does not delete without confirming first", async () => {
    const user = userEvent.setup();
    const deleteAsset = vi.mocked(assetsService.deleteAsset);
    const onSelect = vi.fn();
    renderWithProviders(
      <DocumentCard asset={makeAsset()} isSelected={false} onSelect={onSelect} />,
    );

    await user.click(screen.getByRole("button", { name: "Delete abc_poultry.txt" }));

    expect(screen.getByText('Delete "abc_poultry.txt"?')).toBeInTheDocument();
    expect(deleteAsset).not.toHaveBeenCalled();
    // Opening the confirm dialog is a separate action from selecting
    // the document -- the card's own click handler must not also fire.
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("deletes and invalidates the asset list on confirm", async () => {
    const user = userEvent.setup();
    const deleteAsset = vi.mocked(assetsService.deleteAsset).mockResolvedValue(undefined);
    renderWithProviders(
      <DocumentCard asset={makeAsset()} isSelected={false} onSelect={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Delete abc_poultry.txt" }));
    await user.click(screen.getByRole("button", { name: /delete document/i }));

    await waitFor(() => expect(deleteAsset).toHaveBeenCalledWith("asset-1"));
    await waitFor(() =>
      expect(screen.queryByText('Delete "abc_poultry.txt"?')).not.toBeInTheDocument(),
    );
  });

  it("closes without deleting on cancel", async () => {
    const user = userEvent.setup();
    const deleteAsset = vi.mocked(assetsService.deleteAsset);
    renderWithProviders(
      <DocumentCard asset={makeAsset()} isSelected={false} onSelect={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Delete abc_poultry.txt" }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(deleteAsset).not.toHaveBeenCalled();
    // Awaited rather than asserted synchronously: the dialog animates
    // out, so it outlives the click by the length of its exit spring.
    // Same treatment the delete-path test above already uses.
    await waitFor(() =>
      expect(screen.queryByText('Delete "abc_poultry.txt"?')).not.toBeInTheDocument(),
    );
  });

  it("selecting the card still works independently of the delete control", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderWithProviders(
      <DocumentCard asset={makeAsset()} isSelected={false} onSelect={onSelect} />,
    );

    await user.click(screen.getByText("abc_poultry.txt"));

    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("DocumentCard retry", () => {
  it("offers Retry only on a failed generated report, and retries it", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.retryReport).mockResolvedValue({ asset_id: "asset-1", status: "pending" });

    renderWithProviders(
      <DocumentCard
        asset={makeAsset({ source: "generated", processing_status: "failed" })}
        isSelected={false}
        onSelect={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: /^retry$/i }));

    await waitFor(() => expect(reportsService.retryReport).toHaveBeenCalledWith("asset-1"));
  });

  it("offers no Retry on a failed uploaded document", () => {
    renderWithProviders(
      <DocumentCard
        asset={makeAsset({ source: "upload", processing_status: "failed" })}
        isSelected={false}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /^retry$/i })).not.toBeInTheDocument();
  });

  it("shows an error inline when the retry fails", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.retryReport).mockRejectedValue({
      status: 409,
      message: "Only a failed report can be retried.",
    });

    renderWithProviders(
      <DocumentCard
        asset={makeAsset({ source: "generated", processing_status: "failed" })}
        isSelected={false}
        onSelect={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: /^retry$/i }));

    expect(await screen.findByText("Only a failed report can be retried.")).toBeInTheDocument();
  });
});
