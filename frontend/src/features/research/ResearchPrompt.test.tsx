import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchPrompt } from "@/features/research/ResearchPrompt";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/projects");
vi.mock("@/services/research");

describe("ResearchPrompt submission", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("submits the query against the selected project via the real research API", async () => {
    vi.mocked(projectsService.listProjects).mockResolvedValue([
      {
        id: "project-1",
        owner_id: "owner-1",
        name: "Poultry Market Intelligence",
        description: null,
        project_type: "research",
        status: "active",
        color: null,
        icon: null,
        persona_override: null,
        effective_persona: "researcher",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);
    vi.mocked(researchService.startResearchRun).mockResolvedValue({
      run_id: "run-42",
      status: "pending",
      project_id: "project-1",
      query: "What challenges does ABC Poultry face?",
      created_at: new Date().toISOString(),
    });
    const user = userEvent.setup();

    renderWithProviders(<ResearchPrompt />);
    await screen.findByText("Poultry Market Intelligence");

    await user.selectOptions(screen.getByLabelText("Project"), "project-1");
    await user.type(
      screen.getByLabelText("Question"),
      "What challenges does ABC Poultry face?",
    );
    await user.click(screen.getByRole("button", { name: /research/i }));

    await waitFor(() => {
      expect(researchService.startResearchRun).toHaveBeenCalled();
    });
    expect(vi.mocked(researchService.startResearchRun).mock.calls[0][0]).toMatchObject({
      project_id: "project-1",
      query: "What challenges does ABC Poultry face?",
    });
  });

  it("submits on Enter, while Shift+Enter inserts a new line", async () => {
    vi.mocked(projectsService.listProjects).mockResolvedValue([
      {
        id: "project-1",
        owner_id: "owner-1",
        name: "Poultry Market Intelligence",
        description: null,
        project_type: "research",
        status: "active",
        color: null,
        icon: null,
        persona_override: null,
        effective_persona: "researcher",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);
    vi.mocked(researchService.startResearchRun).mockResolvedValue({
      run_id: "run-43",
      status: "pending",
      project_id: "project-1",
      query: "Line one\nLine two",
      created_at: new Date().toISOString(),
    });
    const user = userEvent.setup();

    renderWithProviders(<ResearchPrompt />);
    await screen.findByText("Poultry Market Intelligence");
    await user.selectOptions(screen.getByLabelText("Project"), "project-1");

    const field = screen.getByLabelText("Question");
    await user.type(field, "Line one{Shift>}{Enter}{/Shift}Line two");
    expect(researchService.startResearchRun).not.toHaveBeenCalled();
    expect(field).toHaveValue("Line one\nLine two");

    await user.type(field, "{Enter}");

    await waitFor(() => {
      expect(researchService.startResearchRun).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(researchService.startResearchRun).mock.calls[0][0]).toMatchObject({
      query: "Line one\nLine two",
    });
  });
});
