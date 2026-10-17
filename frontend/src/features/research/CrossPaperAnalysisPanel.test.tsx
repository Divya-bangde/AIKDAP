import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CrossPaperAnalysisPanel } from "@/features/research/CrossPaperAnalysisPanel";
import * as research from "@/services/research";
import * as assets from "@/services/assets";
import { makeAsset } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/research");
vi.mock("@/services/assets");
// PDF rendering has its own viewer tests; this checks the exact citation contract.
vi.mock("@/features/research/SourceViewer", () => ({ SourceViewer: ({ citation }: { citation: { asset_id: string; chunk_id: string } | null }) => citation && <div role="dialog">{citation.asset_id}/{citation.chunk_id}</div> }));
vi.mock("@/features/research/EvidenceDrawer", () => ({ EvidenceDrawer: () => null }));

const papers = [makeAsset({ id: "primary", title: "Trial A" }), makeAsset({ id: "support", title: "Trial B" }), makeAsset({ id: "foreign", title: "Other project", project_id: "other" })];
const result: research.SavedComparison = {
  primary_paper_id: "primary", supporting_paper_ids: ["support"], artifact_id: "saved-artifact",
  created_at: "2026-10-14T10:00:00Z", goal: { type: "compare", description: "Compare survival" },
  source_versions: [{ asset_id: "primary", checksum: "abc", asset_version: 1, chunk_fingerprint: "chunks", extractor_version: "v2", model_identity: "model", prompt_version: "p2" }],
  status: "completed", insufficiency_reason: null, stale: false, source_provenance: "Real passages",
  gap_resolutions: [], hypotheses: [], comparison_items: [{
    topic: "Survival", primary_claim: "Survival improved 10%", supporting_claims: ["Survival fell 10%"],
    relationship: "CONTRADICTS", evidence: "Effects differ in direction.", certainty: "explicit",
    source_references: [{ paper_id: "primary", paper_title: "Trial A", chunk_id: "primary-chunk", quote: "Survival improved 10%", evidence_scope: "FULL_TEXT" },
      { paper_id: "support", paper_title: "Trial B", chunk_id: "support-chunk", quote: "Survival fell 10%", evidence_scope: "FULL_TEXT" }],
  }],
};

beforeEach(() => { vi.clearAllMocks(); assets.getAsset && vi.mocked(assets.getAsset).mockResolvedValue(papers[1]); });

describe("saved paper comparison", () => {
  it("submits selected real paper IDs and goal, then restores and opens the cited source after refresh", async () => {
    vi.mocked(research.restoreComparison).mockResolvedValue(null);
    vi.mocked(research.comparePapers).mockResolvedValue(result);
    const user = userEvent.setup();
    const view = renderWithProviders(<CrossPaperAnalysisPanel primaryAssetId="primary" projectId="p1" papers={papers} />);
    await user.type(await screen.findByRole("textbox", { name: "Comparison goal" }), "Compare survival");
    await user.click(screen.getByRole("checkbox", { name: "Trial B" }));
    expect(screen.queryByRole("checkbox", { name: "Other project" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Compare papers" }));
    await waitFor(() => expect(research.comparePapers).toHaveBeenCalledWith("primary", "p1", {
      supporting_asset_ids: ["support"], goal: { type: "compare", description: "Compare survival" },
    }));
    expect(await screen.findByText("Survival · CONTRADICTS")).toBeInTheDocument();
    view.unmount();
    vi.mocked(research.restoreComparison).mockResolvedValue(result);
    renderWithProviders(<CrossPaperAnalysisPanel primaryAssetId="primary" projectId="p1" papers={papers} />);
    expect(await screen.findByText("Survival · CONTRADICTS")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Comparison goal" })).toHaveValue("Compare survival");
    await user.click(screen.getByRole("button", { name: "Open source: Trial B" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("support/support-chunk");
    expect(assets.getAsset).toHaveBeenCalledWith("support");
  });

  it("blocks stale citations and preserves explicit insufficient evidence", async () => {
    vi.mocked(research.restoreComparison).mockResolvedValue({ ...result, stale: true });
    const view = renderWithProviders(<CrossPaperAnalysisPanel primaryAssetId="primary" projectId="p1" papers={papers} />);
    expect(await screen.findByRole("button", { name: "Open source: Trial B" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Recompute");
    view.unmount();
    vi.mocked(research.restoreComparison).mockResolvedValue({ ...result, status: "insufficient_evidence", comparison_items: [], insufficiency_reason: "Missing extracted passages" });
    renderWithProviders(<CrossPaperAnalysisPanel primaryAssetId="primary" projectId="p1" papers={papers} />);
    expect(await screen.findByText("Insufficient evidence: Missing extracted passages")).toBeInTheDocument();
    expect(screen.queryByText("Survival · CONTRADICTS")).not.toBeInTheDocument();
  });
});
