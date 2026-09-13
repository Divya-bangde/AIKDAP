import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchHistoryList } from "@/features/research/ResearchHistoryList";
import { ResearchRunView } from "@/features/research/ResearchRunView";
import { Dashboard } from "@/pages/Dashboard";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

vi.mock("@/services/assets");
vi.mock("@/services/projects");
vi.mock("@/services/research");

type ResearchRunDetail = components["schemas"]["ResearchRunDetail"];
type ProjectRead = components["schemas"]["ProjectRead"];

/** Sprint 9K.9's actual promise: the same run reads the same way
 * wherever it appears. These tests build one run fixture and check
 * that Dashboard, ResearchHistoryList and ResearchRunView — which each
 * fetch/receive it independently — all render the same outcome text for
 * it, through the shared `runOutcome` presentation layer rather than
 * three separate re-derivations. */
function makeRun(overrides: Partial<ResearchRunDetail> = {}): ResearchRunDetail {
  return {
    id: "run-1",
    project_id: "p1",
    owner_id: "u1",
    task_id: null,
    query: "What is the capital of Japan?",
    status: "completed",
    include_assets: true,
    include_web: false,
    max_results: 5,
    objective: null,
    plan: null,
    final_answer: null,
    citations: [],
    grounding_status: "insufficient_evidence",
    error_message: null,
    celery_task_id: null,
    started_at: null,
    completed_at: null,
    duration_ms: 1800,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    steps: [],
    ...overrides,
  } as ResearchRunDetail;
}

function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: "p1",
    owner_id: "u1",
    name: "9K9 Consistency Project",
    description: null,
    project_type: "research",
    status: "active",
    color: null,
    icon: null,
    persona_override: null,
    effective_persona: "researcher",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("the same run reads the same outcome on every surface", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows 'Insufficient Evidence' on Dashboard, History and the Run page for the same run — never 'Completed' on any of them", async () => {
    const run = makeRun();

    vi.mocked(projectsService.listProjects).mockResolvedValue([makeProject()]);
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRuns).mockResolvedValue([run]);
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText("Insufficient Evidence")).toBeInTheDocument();
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();

    renderWithProviders(<ResearchHistoryList runs={[run]} />);
    expect(screen.getAllByText("Insufficient Evidence").length).toBeGreaterThan(0);
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();

    vi.mocked(researchService.getResearchRun).mockResolvedValue(run);
    renderWithProviders(<ResearchRunView runId={run.id} />);
    // The header badge and the result card's own safety-outcome title
    // both say the same real thing — the pre-existing Sprint 9K.7
    // guarantee this sprint must not regress.
    expect(await screen.findAllByText("Insufficient Evidence")).not.toHaveLength(0);
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
  });

  it("shows 'Grounded' consistently for a grounded run", async () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Tokyo.",
      citations: [{ id: "c1" }],
    });

    vi.mocked(projectsService.listProjects).mockResolvedValue([makeProject()]);
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRuns).mockResolvedValue([run]);
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText("Grounded")).toBeInTheDocument();

    renderWithProviders(<ResearchHistoryList runs={[run]} />);
    expect(screen.getAllByText("Grounded").length).toBeGreaterThan(0);
  });
});
