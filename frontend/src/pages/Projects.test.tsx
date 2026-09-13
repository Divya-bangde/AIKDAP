import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Projects } from "@/pages/Projects";
import * as projectsService from "@/services/projects";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

vi.mock("@/services/projects");

type ProjectRead = components["schemas"]["ProjectRead"];

function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    owner_id: "owner-1",
    name: "Poultry Market Intelligence",
    description: "AI research project for Indian poultry industry analysis.",
    project_type: "research",
    status: "active",
    color: "#2563EB",
    icon: "brain",
    persona_override: null,
    effective_persona: "researcher",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("Projects page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the empty state when there are no projects", async () => {
    vi.mocked(projectsService.listProjects).mockResolvedValue([]);

    renderWithProviders(<Projects />);

    expect(await screen.findByText("No research projects yet.")).toBeInTheDocument();
  });

  it("lists real projects returned by the API", async () => {
    vi.mocked(projectsService.listProjects).mockResolvedValue([makeProject()]);

    renderWithProviders(<Projects />);

    expect(await screen.findByText("Poultry Market Intelligence")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("creates a project using the exact backend schema and refreshes the list", async () => {
    vi.mocked(projectsService.listProjects).mockResolvedValue([]);
    vi.mocked(projectsService.createProject).mockResolvedValue(makeProject());
    const user = userEvent.setup();

    renderWithProviders(<Projects />);
    await screen.findByText("No research projects yet.");

    await user.click(screen.getByRole("button", { name: /create project/i }));
    await user.type(screen.getByLabelText("Name"), "Poultry Market Intelligence");
    await user.type(
      screen.getByLabelText("Description"),
      "AI research project for Indian poultry industry analysis.",
    );
    await user.click(screen.getByRole("button", { name: "Create Project" }));

    await waitFor(() => {
      expect(projectsService.createProject).toHaveBeenCalled();
    });
    expect(vi.mocked(projectsService.createProject).mock.calls[0][0]).toEqual({
      name: "Poultry Market Intelligence",
      description: "AI research project for Indian poultry industry analysis.",
      project_type: "research",
    });
  });
});
