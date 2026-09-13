import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/layouts/AppShell";
import * as authService from "@/services/auth";
import { useAuthStore } from "@/store/auth-store";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/auth");

describe("AppShell", () => {
  afterEach(() => {
    useAuthStore.getState().clear();
    vi.restoreAllMocks();
  });

  it("shows navigation links and clears the session on logout", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });
    vi.mocked(authService.currentUser).mockResolvedValue({
      id: "u1",
      email: "researcher@example.com",
      full_name: null,
      is_active: true,
      persona: "researcher",
      created_at: new Date().toISOString(),
    });
    const user = userEvent.setup();

    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<div>Dashboard content</div>} />
        </Route>
      </Routes>,
    );

    expect(screen.getAllByText("AIKDAP").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Dashboard")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Projects")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Research")[0]).toBeInTheDocument();
    expect(screen.queryByText("System Health")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AIKDAP home" })).toHaveAttribute("href", "/home");

    await waitFor(() => {
      expect(screen.getByText("researcher@example.com")).toBeInTheDocument();
    });

    // One logout in the sidebar, one in the phone-width top bar.
    await user.click(screen.getAllByRole("button", { name: /log out/i })[0]);

    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("collapses the sidebar to icons and remembers it", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });
    vi.mocked(authService.currentUser).mockResolvedValue({
      id: "u1",
      email: "researcher@example.com",
      full_name: null,
      is_active: true,
      persona: "researcher",
      created_at: new Date().toISOString(),
    });
    const user = userEvent.setup();

    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<div>Dashboard content</div>} />
        </Route>
      </Routes>,
    );

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    expect(localStorage.getItem("aikdap-sidebar-collapsed")).toBe("1");
    localStorage.removeItem("aikdap-sidebar-collapsed");
  });

  it("offers a back button only on inner pages", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });
    vi.mocked(authService.currentUser).mockResolvedValue({
      id: "u1",
      email: "researcher@example.com",
      full_name: null,
      is_active: true,
      persona: "researcher",
      created_at: new Date().toISOString(),
    });
    const user = userEvent.setup();

    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/projects" element={<div>Projects list</div>} />
          <Route path="/projects/:id" element={<div>One project</div>} />
        </Route>
      </Routes>,
      { route: "/projects/p1" },
    );

    await user.click(screen.getByRole("button", { name: "Go back" }));

    expect(await screen.findByText("Projects list")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Go back" })).not.toBeInTheDocument();
  });

  it("lets its content column shrink below its content's intrinsic width", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });
    vi.mocked(authService.currentUser).mockResolvedValue({
      id: "u1",
      email: "researcher@example.com",
      full_name: null,
      is_active: true,
      persona: "researcher",
      created_at: new Date().toISOString(),
    });

    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<div>Dashboard content</div>} />
        </Route>
      </Routes>,
    );

    /* A flex item defaults to `min-width: auto` and so refuses to
     * shrink below its content. Without `min-w-0` this column stayed
     * 450px wide inside a 390px viewport and every authenticated page
     * scrolled sideways (measured in a real browser, Sprint 9K.6).
     *
     * jsdom does no layout, so the overflow itself cannot be asserted
     * here — what this pins is the class that prevents it, which is
     * the thing a future edit would silently drop. */
    const column = document.querySelector("main")?.parentElement;
    expect(column).not.toBeNull();
    expect(column).toHaveClass("min-w-0");
  });

  it("moves one navigation indicator between links rather than swapping four", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });
    vi.mocked(authService.currentUser).mockResolvedValue({
      id: "u1",
      email: "researcher@example.com",
      full_name: null,
      is_active: true,
      persona: "researcher",
      created_at: new Date().toISOString(),
    });
    const user = userEvent.setup();

    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<div>Dashboard content</div>} />
          <Route path="/projects" element={<div>Projects content</div>} />
        </Route>
      </Routes>,
    );

    /* The indicator is a single element carrying a shared `layoutId`,
     * so Motion animates it from one link to the next. What a test can
     * hold onto is the invariant that makes that possible: exactly one
     * of them exists per navigation bar, and it lives inside whichever
     * link is currently active. */
    const indicator = () => document.querySelectorAll(".bg-accent.absolute");
    const dashboardLink = screen.getAllByRole("link", { name: "Dashboard" })[0];
    const projectsLink = screen.getAllByRole("link", { name: "Projects" })[0];

    await waitFor(() => expect(dashboardLink.querySelector(".bg-accent")).not.toBeNull());
    expect(projectsLink.querySelector(".bg-accent")).toBeNull();

    await user.click(projectsLink);

    await waitFor(() => expect(projectsLink.querySelector(".bg-accent")).not.toBeNull());
    expect(dashboardLink.querySelector(".bg-accent")).toBeNull();
    // One indicator per navigation bar (the shell renders a desktop and
    // a compact bar), never one per link.
    expect(indicator().length).toBe(2);
  });
});
