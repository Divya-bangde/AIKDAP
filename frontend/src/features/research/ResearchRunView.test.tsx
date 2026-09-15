import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchRunView } from "@/features/research/ResearchRunView";
import * as researchService from "@/services/research";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

vi.mock("@/services/research");

type ResearchRunDetail = components["schemas"]["ResearchRunDetail"];

function makeRun(overrides: Partial<ResearchRunDetail>): ResearchRunDetail {
  return {
    id: "run-1",
    project_id: "project-1",
    owner_id: "owner-1",
    task_id: null,
    query: "What challenges does ABC Poultry face?",
    status: "running",
    include_assets: true,
    include_web: false,
    max_results: 5,
    objective: null,
    plan: null,
    final_answer: null,
    citations: null,
    grounding_status: null,
    error_message: null,
    celery_task_id: null,
    started_at: new Date().toISOString(),
    completed_at: null,
    duration_ms: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    steps: [],
    ...overrides,
  };
}

describe("ResearchRunView polling (Phase 19)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders real backend steps while running, then polls again", async () => {
    const getRun = vi.mocked(researchService.getResearchRun);
    getRun.mockResolvedValueOnce(
      makeRun({
        steps: [
          {
            id: "s1",
            run_id: "run-1",
            asset_id: null,
            attempt: 1,
            step_index: 0,
            node_name: "planner",
            title: "Plan the research run",
            status: "completed",
            summary: null,
            output_payload: null,
            error_message: null,
            started_at: null,
            completed_at: null,
            duration_ms: null,
            created_at: new Date().toISOString(),
          },
        ],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    // Sprint 9K.8: the plain-language presentation title is now the
    // primary text; the backend's own "Plan the research run" moved
    // into the collapsed technical detail (covered in
    // ResearchPipeline.test.tsx).
    expect(await screen.findByText("Understanding your question")).toBeInTheDocument();
    // Sprint 9K.9: the header badge now reads through the shared
    // `runOutcome` presentation layer, which renders a non-terminal run
    // as "In Progress" rather than the bare backend value "running".
    expect(screen.getByText("In Progress")).toBeInTheDocument();
    await waitFor(() => expect(getRun).toHaveBeenCalledTimes(1));
  });

  it("stops polling and shows the grounded result once the backend reports completed", async () => {
    vi.mocked(researchService.getResearchRun).mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "grounded",
        final_answer: "ABC Poultry faces feed cost inflation.",
        citations: [{ id: "c1", title: "ABC Poultry FY2026" }],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    expect(await screen.findByText("ABC Poultry faces feed cost inflation.")).toBeInTheDocument();
    // "Grounded" now appears twice: the page header (Sprint 9K.7 — the
    // run's real outcome, not just the fact the job finished) and the
    // result card's own status badge (pre-existing). Both are the same
    // honest answer to the same question, so two is correct here.
    expect(screen.getAllByText("Grounded").length).toBe(2);
  });

  it("collapses the pipeline once the run completes, keeping the answer primary", async () => {
    vi.mocked(researchService.getResearchRun).mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "grounded",
        final_answer: "ABC Poultry faces feed cost inflation.",
        citations: [{ id: "c1", title: "ABC Poultry FY2026" }],
        steps: [
          {
            id: "s1",
            run_id: "run-1",
            asset_id: null,
            attempt: 1,
            step_index: 0,
            node_name: "planner",
            title: "Plan the research run",
            status: "completed",
            summary: null,
            output_payload: null,
            error_message: null,
            started_at: null,
            completed_at: null,
            duration_ms: null,
            created_at: new Date().toISOString(),
          },
        ],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    expect(await screen.findByText("ABC Poultry faces feed cost inflation.")).toBeInTheDocument();
    const disclosure = screen.getByText("AI research pipeline · 1 step").closest("details");
    // Asserted on `open` rather than visibility: the cards fade in via
    // motion, so opacity in jsdom says nothing about the collapse itself.
    expect(disclosure).not.toBeNull();
    expect(disclosure).not.toHaveAttribute("open");
    // Still in the DOM, one click away, inside that closed disclosure.
    expect(screen.getByText("Understanding your question").closest("details")).toBe(disclosure);
  });

  it("shows the run's real outcome in the header, not just that the job finished (Sprint 9K.7)", async () => {
    vi.mocked(researchService.getResearchRun).mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "insufficient_evidence",
        final_answer: null,
        citations: [],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    /* Two matches: the header's own badge (Sprint 9K.7) and the result
     * card's pre-existing safety-outcome title. Both are the same
     * honest answer to "what happened", which is the point — a run
     * that produced no answer must never carry the same badge text
     * ("Completed") as one that did. */
    await waitFor(() => expect(screen.getAllByText("Insufficient Evidence").length).toBe(2));
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
  });

  it("shows the real backend failure message, not a generic error, when a run fails", async () => {
    vi.mocked(researchService.getResearchRun).mockResolvedValue(
      makeRun({
        status: "failed",
        error_message: "LLMServiceUnavailableError: all providers are temporarily unavailable.",
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    expect(
      await screen.findByText(
        "LLMServiceUnavailableError: all providers are temporarily unavailable.",
      ),
    ).toBeInTheDocument();
    // A failed run must never render the success result card.
    expect(screen.queryByText("Grounded Intelligence")).not.toBeInTheDocument();
  });
});

describe("ResearchRunView re-run linkage (Milestone 10 step 3)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows a 're-run with N added papers' banner when this run has a parent", async () => {
    vi.mocked(researchService.getResearchRun).mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "grounded",
        final_answer: "The answer.",
        citations: [],
        parent_run_id: "run-0",
        added_paper_count: 2,
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    expect(await screen.findByText(/re-run with 2 added papers/i)).toBeInTheDocument();
  });

  it("does not show the banner for a run with no parent", async () => {
    vi.mocked(researchService.getResearchRun).mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "grounded",
        final_answer: "The answer.",
        citations: [],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);
    await screen.findByText("What challenges does ABC Poultry face?");

    expect(screen.queryByText(/re-run with/i)).not.toBeInTheDocument();
  });

  it("keeps polling when every paper is final but the rerun link has not appeared yet (final review Fix 4)", async () => {
    // The import chord finishes writing every paper's final
    // import_status a moment BEFORE the separate finalize_paper_import
    // callback actually creates and links the child run. isTerminal
    // must therefore keep polling (not stop) while a paper is "added"
    // but rerun_run_id is still null -- proven here by advancing the
    // poll interval and observing a second fetch, the same way
    // usePolling's own refetchInterval is driven in production.
    vi.useFakeTimers();
    const getRun = vi.mocked(researchService.getResearchRun);
    getRun.mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "grounded",
        final_answer: "The answer.",
        citations: [],
        rerun_run_id: null,
        suggested_papers: [
          {
            openalex_id: "https://openalex.org/W1",
            title: "A Suggested Paper",
            authors: [],
            year: null,
            cited_by_count: 0,
            landing_url: "https://example.org/landing",
            oa_pdf_url: "https://example.org/a.pdf",
            relevance_note: "",
            import_status: "added",
          },
        ] as unknown as ResearchRunDetail["suggested_papers"],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);
    await vi.waitFor(() => expect(getRun.mock.calls.length).toBeGreaterThanOrEqual(1));
    const callsBeforeAdvancing = getRun.mock.calls.length;

    await vi.advanceTimersByTimeAsync(2000);
    await vi.waitFor(() => expect(getRun.mock.calls.length).toBeGreaterThan(callsBeforeAdvancing));

    vi.useRealTimers();
  });

  it("stops polling and shows a timeout message if the import never reaches a terminal state (residual 4a)", async () => {
    vi.useFakeTimers();
    const getRun = vi.mocked(researchService.getResearchRun);
    getRun.mockResolvedValue(
      makeRun({
        status: "completed",
        grounding_status: "grounded",
        final_answer: "The answer.",
        citations: [],
        rerun_run_id: null,
        suggested_papers: [
          {
            openalex_id: "https://openalex.org/W1",
            title: "A Suggested Paper",
            authors: [],
            year: null,
            cited_by_count: 0,
            landing_url: "https://example.org/landing",
            oa_pdf_url: "https://example.org/a.pdf",
            relevance_note: "",
            import_status: "queued",
          },
        ] as unknown as ResearchRunDetail["suggested_papers"],
      }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);
    await vi.waitFor(() => expect(getRun.mock.calls.length).toBeGreaterThanOrEqual(1));
    const callsBeforeTimeout = getRun.mock.calls.length;

    await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 1000);
    await vi.waitFor(() =>
      expect(screen.getByText("Import is taking longer than expected")).toBeInTheDocument(),
    );

    const callsAtTimeout = getRun.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(getRun.mock.calls.length).toBe(callsAtTimeout);
    expect(callsAtTimeout).toBeGreaterThan(callsBeforeTimeout);

    vi.useRealTimers();
  }, 20000);
});
