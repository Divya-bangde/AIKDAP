import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectHeader } from "@/features/projects/ProjectHeader";
import { aiProfile, makeAsset, makeProject, makeUser } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as projectsService from "@/services/projects";
import { useAuthStore } from "@/store/auth-store";

vi.mock("@/services/projects");

describe("ProjectHeader — Start Research", () => {
  afterEach(() => vi.restoreAllMocks());

  it("warns and offers upload when the project has no documents", async () => {
    const onRequestUpload = vi.fn();
    const user = userEvent.setup();

    renderWithProviders(
      <ProjectHeader project={makeProject()} assets={[]} runs={[]} onRequestUpload={onRequestUpload} />,
    );
    await user.click(screen.getByRole("button", { name: /start research/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/upload documents first/i);
    await user.click(screen.getByRole("button", { name: "Upload documents" }));
    expect(onRequestUpload).toHaveBeenCalled();
  });

  it("warns when documents exist but none has finished processing", async () => {
    const user = userEvent.setup();
    const processing = makeAsset({
      ai_profile: aiProfile({ status: "completed", embedding_status: "processing" }),
    });

    renderWithProviders(
      <ProjectHeader project={makeProject()} assets={[processing]} runs={[]} onRequestUpload={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /start research/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/still processing/i);
  });

  it("starts research without a warning once a document is embedded", async () => {
    const user = userEvent.setup();

    renderWithProviders(
      <ProjectHeader project={makeProject()} assets={[makeAsset()]} runs={[]} onRequestUpload={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: /start research/i }));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ProjectHeader — persona", () => {
  afterEach(() => {
    useAuthStore.getState().clear();
    vi.restoreAllMocks();
  });

  it("overrides the persona for this project and can return to the default", async () => {
    useAuthStore.setState({ user: makeUser({ persona: "student" }) });
    vi.mocked(projectsService.updateProject).mockResolvedValue(makeProject());
    const user = userEvent.setup();
    // Starts overridden so both a new override and "use my default" are
    // real changes; the select is controlled by the `project` prop, so it
    // snaps back to "builder" between the two selections.
    const overridden = makeProject({ persona_override: "builder", effective_persona: "builder" });

    renderWithProviders(
      <ProjectHeader project={overridden} assets={[]} runs={[]} onRequestUpload={vi.fn()} />,
    );
    const select = screen.getByLabelText("Project persona");
    expect(select).toHaveValue("builder");
    expect(screen.getByRole("option", { name: "Use my default (Student)" })).toBeInTheDocument();

    await user.selectOptions(select, "researcher");
    await waitFor(() =>
      expect(projectsService.updateProject).toHaveBeenCalledWith("p1", { persona_override: "researcher" }),
    );

    await user.selectOptions(select, "");
    await waitFor(() =>
      expect(projectsService.updateProject).toHaveBeenLastCalledWith("p1", { persona_override: null }),
    );
  });
});

describe("ProjectHeader — Generate synopsis", () => {
  afterEach(() => vi.restoreAllMocks());

  it("does not show the button for non-student personas", () => {
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "researcher" })}
        assets={[]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /generate synopsis/i })).not.toBeInTheDocument();
  });

  it("shows the button only for the student persona", () => {
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "student" })}
        assets={[]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /generate synopsis/i })).toBeInTheDocument();
  });

  it("opens the synopsis dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "student" })}
        assets={[]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /generate synopsis/i }));

    expect(screen.getByText("Creates a report from this project's processed documents.")).toBeInTheDocument();
  });
});

describe("ProjectHeader — Get build plan", () => {
  afterEach(() => vi.restoreAllMocks());

  it.each(["researcher", "student"] as const)(
    "does not show the button for the %s persona",
    (persona) => {
      renderWithProviders(
        <ProjectHeader
          project={makeProject({ effective_persona: persona })}
          assets={[]}
          runs={[]}
          onRequestUpload={vi.fn()}
        />,
      );
      expect(screen.queryByRole("button", { name: /get build plan/i })).not.toBeInTheDocument();
    },
  );

  it("shows the button for the builder persona", () => {
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "builder" })}
        assets={[]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /get build plan/i })).toBeInTheDocument();
  });

  it("opens the build-plan dialog with the project's papers", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ProjectHeader
        project={makeProject({ effective_persona: "builder" })}
        assets={[makeAsset({ id: "a1", title: "First paper.pdf" })]}
        runs={[]}
        onRequestUpload={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /get build plan/i }));

    expect(screen.getByRole("checkbox", { name: /first paper\.pdf/i })).toBeChecked();
  });
});
