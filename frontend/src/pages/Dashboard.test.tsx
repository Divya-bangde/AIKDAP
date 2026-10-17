import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/pages/Dashboard";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";
import { makeAsset, makeProject, makeRun } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/assets");
vi.mock("@/services/projects");

beforeEach(() => {
  vi.mocked(projectsService.getWorkspaceSummary).mockResolvedValue({ projects: 1, assets: 1, documents: 1, runs: 1, grounded_answers: 1 });
});
vi.mock("@/services/research");

describe("Dashboard — Recent Research Runs", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the project and duration for a completed run", async () => {
    vi.mocked(projectsService.listProjectsPage).mockResolvedValue({ items: [
      makeProject({ id: "p1", name: "Poultry Intelligence" }),
    ], next_cursor: null });
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRunsPage).mockResolvedValue({ items: [
      makeRun({ duration_ms: 4200 }),
    ], next_cursor: null });

    renderWithProviders(<Dashboard />);

    expect(await screen.findByText("What challenges does ABC Poultry face?")).toBeInTheDocument();
    // Once as a project entry, once as this run's attribution.
    expect(screen.getAllByText("Poultry Intelligence").length).toBeGreaterThan(0);
    expect(screen.getByText("4.2 s")).toBeInTheDocument();
  });

  it("shows a real citation count on a grounded run, never a fabricated one (Sprint 9K.9)", async () => {
    vi.mocked(projectsService.listProjectsPage).mockResolvedValue({ items: [makeProject()], next_cursor: null });
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRunsPage).mockResolvedValue({ items: [
      makeRun({ citations: [{ id: "c1" }], grounding_status: "grounded" }),
    ], next_cursor: null });

    renderWithProviders(<Dashboard />);

    expect(await screen.findByText(/1 citation\b/)).toBeInTheDocument();
  });

  it("never claims a citation count for a run that has none", async () => {
    vi.mocked(projectsService.listProjectsPage).mockResolvedValue({ items: [makeProject()], next_cursor: null });
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRunsPage).mockResolvedValue({ items: [
      makeRun({ citations: [], grounding_status: "grounded" }),
    ], next_cursor: null });

    renderWithProviders(<Dashboard />);

    expect(await screen.findByText("What challenges does ABC Poultry face?")).toBeInTheDocument();
    expect(screen.queryByText(/citation/)).not.toBeInTheDocument();
  });

  it("shows the run's real outcome, not just that the job finished", async () => {
    vi.mocked(projectsService.listProjectsPage).mockResolvedValue({ items: [makeProject()], next_cursor: null });
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRunsPage).mockResolvedValue({ items: [
      makeRun({ grounding_status: "insufficient_evidence" }),
    ], next_cursor: null });

    renderWithProviders(<Dashboard />);

    expect(await screen.findByText("Insufficient Evidence")).toBeInTheDocument();
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
  });
});

describe("Dashboard — keyboard focus order", () => {
  afterEach(() => vi.restoreAllMocks());

  it("puts no inert stops in the tab order", async () => {
    vi.mocked(projectsService.listProjectsPage).mockResolvedValue({ items: [makeProject()], next_cursor: null });
    vi.mocked(assetsService.listAssets).mockResolvedValue([makeAsset()]);
    vi.mocked(researchService.listResearchRunsPage).mockResolvedValue({ items: [makeRun()], next_cursor: null });

    const { container } = renderWithProviders(<Dashboard />);
    await screen.findByText("What challenges does ABC Poultry face?");

    /* Motion gives anything carrying `whileTap` a `tabIndex` of 0, so
     * the KPI tiles and card wrappers used to be focusable while doing
     * nothing on Enter (Sprint 9K.6). Every positive-tabindex element
     * must be a real control, or contain one. */
    const focusable = [...container.querySelectorAll("[tabindex]")].filter(
      (el) => Number(el.getAttribute("tabindex")) >= 0,
    );

    for (const el of focusable) {
      const isControl = ["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(el.tagName);
      const hasRole = el.getAttribute("role") !== null;
      expect(
        isControl || hasRole,
        `${el.tagName}.${el.className} is focusable but is not a control`,
      ).toBe(true);
    }
  });
});
