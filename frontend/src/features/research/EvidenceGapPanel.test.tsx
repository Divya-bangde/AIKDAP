import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EvidenceGapPanel } from "@/features/research/EvidenceGapPanel";
import * as assetsService from "@/services/assets";
import * as researchService from "@/services/research";
import { aiProfile } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

vi.mock("@/services/assets");
vi.mock("@/services/research");

type AssetRead = components["schemas"]["AssetRead"];
type ResearchDocumentUnderstanding = components["schemas"]["ResearchDocumentUnderstanding"];

function makeAsset(): AssetRead {
  return {
    id: "asset-1",
    project_id: "project-1",
    owner_id: "owner-1",
    title: "doc",
    description: null,
    asset_type: "other",
    status: "active",
    mime_type: "application/pdf",
    file_name: "paper.pdf",
    file_extension: ".pdf",
    file_size: 2048,
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
  };
}

describe("EvidenceGapPanel", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("renders only the real gaps analysis.py returned, with the reason and classification badges", async () => {
    vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [makeAsset()], next_cursor: null });
    const analysis: ResearchDocumentUnderstanding = {
      title: "paper",
      objectives: [],
      research_questions: [],
      models: [],
      algorithms: [],
      equations: [],
      variables: [],
      evaluation_metrics: [],
      limitations: [],
      future_work: [],
      explicit_assumptions: [],
      missing_information: [
        {
          gap_type: "Detection rate for the specific traffic channel asked about",
          classification: "required",
          description: "The paper does not report a rate for this exact channel.",
          why_needed: "The question asks for a number the source never states.",
        },
      ],
      conflicts: [],
      sufficiency: "insufficient",
      sufficiency_reason: "The retrieved evidence does not name this specific channel.",
    };
    vi.mocked(researchService.analyzeResearchDocument).mockResolvedValue(analysis);

    renderWithProviders(
      <EvidenceGapPanel
        projectId="project-1"
        query="What rate did it achieve against X?"
        runId="run-1"
      />,
    );

    expect(
      await screen.findByText("The retrieved evidence does not name this specific channel."),
    ).toBeInTheDocument();
    expect(screen.getByText("Required")).toBeInTheDocument();
    expect(
      screen.getByText("Detection rate for the specific traffic channel asked about"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/The question asks for a number the source never states\./),
    ).toBeInTheDocument();
  });

  it("shows the honest empty state, not invented gap text, when analysis found nothing specific", async () => {
    vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [makeAsset()], next_cursor: null });
    vi.mocked(researchService.analyzeResearchDocument).mockResolvedValue({
      title: "paper",
      objectives: [],
      research_questions: [],
      models: [],
      algorithms: [],
      equations: [],
      variables: [],
      evaluation_metrics: [],
      limitations: [],
      future_work: [],
      explicit_assumptions: [],
      missing_information: [],
      conflicts: [],
      sufficiency: "partially_sufficient",
      sufficiency_reason: "Most of the question is answered; one sub-part is not.",
    });

    renderWithProviders(<EvidenceGapPanel projectId="project-1" query="q" runId="run-1" />);

    expect(
      await screen.findByText("No specific gap was identified for this question."),
    ).toBeInTheDocument();
  });

  it("still offers the upload action when the project has no asset to analyze", async () => {
    vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [], next_cursor: null });

    renderWithProviders(<EvidenceGapPanel projectId="project-1" query="q" runId="run-1" />);

    await waitFor(() => expect(assetsService.listAssetsPage).toHaveBeenCalledWith("project-1", null));
    expect(researchService.analyzeResearchDocument).not.toHaveBeenCalled();
    expect(await screen.findByText(/drag/i)).toBeInTheDocument();
  });

  it("analyzes the newest source paper, skipping newer generated reports and datasets", async () => {
    vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [
      { ...makeAsset(), id: "report-1", asset_type: "report", source: "generated" },
      { ...makeAsset(), id: "dataset-1", asset_type: "dataset" },
      makeAsset(),
    ], next_cursor: null });

    renderWithProviders(<EvidenceGapPanel projectId="project-1" query="q" runId="run-1" />);

    await waitFor(() =>
      expect(researchService.analyzeResearchDocument).toHaveBeenCalledWith("asset-1", "project-1", {
        goal: { type: "answer_question", description: "q" },
      }),
    );
  });

  // Sprint 16 Phase 8.13: the opt-in "answer from general knowledge"
  // control. `analyzeResearchDocument` is left unresolved in these
  // tests -- they exercise the unsourced control, not the gap list.
  describe("answering from general knowledge (Sprint 16 Phase 8.13)", () => {
    function renderPanel() {
      vi.mocked(assetsService.listAssetsPage).mockResolvedValue({ items: [], next_cursor: null });
      return renderWithProviders(
        <EvidenceGapPanel projectId="project-1" query="What is X?" runId="run-1" />,
      );
    }

    it("never calls the unsourced endpoint until the control is pressed", async () => {
      renderPanel();

      await screen.findByRole("button", { name: "Answer from general knowledge instead" });
      expect(researchService.createUnsourcedAnswer).not.toHaveBeenCalled();
    });

    it("renders the returned answer in a visually distinct container, never a badge on a normal answer", async () => {
      vi.mocked(researchService.createUnsourcedAnswer).mockResolvedValue({
        id: "run-2",
        project_id: "project-1",
        owner_id: "owner-1",
        task_id: null,
        query: "What is X?",
        status: "completed",
        include_assets: false,
        include_web: false,
        max_results: 5,
        objective: null,
        plan: null,
        final_answer:
          "**Not in your uploaded papers. From general knowledge:**\n\nX is generally understood as Y.\n\n## To make this citable\nYou would need a source establishing: Z.\n",
        citations: [],
        grounding_status: "unsourced",
        error_message: null,
        celery_task_id: null,
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        duration_ms: 500,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      const user = userEvent.setup();
      renderPanel();

      await user.click(
        await screen.findByRole("button", { name: "Answer from general knowledge instead" }),
      );

      expect(researchService.createUnsourcedAnswer).toHaveBeenCalledWith("run-1");
      expect(await screen.findByText("X is generally understood as Y.")).toBeInTheDocument();
      // The container's own heading, and the answer's embedded
      // disclosure line, both say "From general knowledge" -- by
      // design, since the disclosure must survive independently of
      // the container (see the copy test below).
      expect(screen.getAllByText(/From general knowledge/).length).toBeGreaterThanOrEqual(2);
      // Framed as a research lead, not an answer -- the disclosure text
      // itself carries the boundary, not merely a badge next to it.
      expect(screen.getByText(/To make this citable/)).toBeInTheDocument();
      // The control is replaced, not duplicated, once an answer exists.
      expect(
        screen.queryByRole("button", { name: "Answer from general knowledge instead" }),
      ).not.toBeInTheDocument();
    });

    it("copies the disclaimer prepended into the copied text, not merely shown alongside it", async () => {
      const finalAnswer =
        "**Not in your uploaded papers. From general knowledge:**\n\nX is Y.\n\n## To make this citable\nYou would need a source establishing: Z.\n";
      vi.mocked(researchService.createUnsourcedAnswer).mockResolvedValue({
        id: "run-2",
        project_id: "project-1",
        owner_id: "owner-1",
        task_id: null,
        query: "What is X?",
        status: "completed",
        include_assets: false,
        include_web: false,
        max_results: 5,
        objective: null,
        plan: null,
        final_answer: finalAnswer,
        citations: [],
        grounding_status: "unsourced",
        error_message: null,
        celery_task_id: null,
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        duration_ms: 500,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      // `userEvent.setup()` installs its own clipboard stub on
      // `navigator.clipboard` for copy/paste simulation, which would
      // silently replace a mock assigned before this call -- so the
      // mock is layered on top of it afterwards instead.
      const user = userEvent.setup();
      const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
      renderPanel();

      await user.click(
        await screen.findByRole("button", { name: "Answer from general knowledge instead" }),
      );
      await screen.findByText(/X is Y\./);
      await user.click(screen.getByRole("button", { name: "Copy" }));

      expect(writeText).toHaveBeenCalledWith(finalAnswer);
      expect(writeText.mock.calls[0][0]).toContain("Not in your uploaded papers");
    });

    it("shows an error rather than a fabricated answer when the model call fails", async () => {
      vi.mocked(researchService.createUnsourcedAnswer).mockRejectedValue(new Error("502"));
      const user = userEvent.setup();
      renderPanel();

      await user.click(
        await screen.findByRole("button", { name: "Answer from general knowledge instead" }),
      );

      expect(
        await screen.findByText(/Could not generate an unsourced answer/),
      ).toBeInTheDocument();
    });
  });
});
