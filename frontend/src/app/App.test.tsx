import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app/App";
import * as authService from "@/services/auth";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";
import { useAuthStore } from "@/store/auth-store";
import { useThemeStore } from "@/store/theme-store";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/auth");
vi.mock("@/services/assets");
vi.mock("@/services/projects");
vi.mock("@/services/research");

function mockSystemDark(prefersDark: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("prefers-color-scheme: dark") ? prefersDark : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

describe("App routing at /", () => {
  beforeEach(() => {
    mockSystemDark(false);
    useAuthStore.setState({ accessToken: null, refreshToken: null, user: null });
    useThemeStore.setState({ preference: null, override: null });
    document.documentElement.classList.remove("dark");

    vi.mocked(projectsService.listProjects).mockResolvedValue([]);
    vi.mocked(assetsService.listAssets).mockResolvedValue([]);
    vi.mocked(researchService.listResearchRuns).mockResolvedValue([]);
    vi.mocked(authService.currentUser).mockResolvedValue({
      id: "u1",
      email: "researcher@example.com",
      full_name: null,
      is_active: true,
      persona: "researcher",
      created_at: new Date().toISOString(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("serves the cinematic entry to an unauthenticated visitor", async () => {
    renderWithProviders(<App />, { route: "/" });

    expect(screen.getByRole("heading", { level: 1, name: "AIKDAP" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /enter aikdap/i })).toBeInTheDocument();
    // Entry is dark-only regardless of preference.
    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(true);
    });
  });

  it("serves the Command Center at the same URL once authenticated", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });

    renderWithProviders(<App />, { route: "/" });

    expect(await screen.findByText("Command Center")).toBeInTheDocument();
    // No landing content leaks into the authenticated view.
    expect(screen.queryByRole("link", { name: /enter aikdap/i })).not.toBeInTheDocument();
    // And the app is back on the user's own theme, not the entry's dark.
    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(false);
    });
  });

  it("serves the landing page at /home to a signed-in user, with a way back in", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });

    renderWithProviders(<App />, { route: "/home" });

    expect(screen.getByRole("heading", { level: 1, name: "AIKDAP" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open workspace" })).toHaveAttribute("href", "/");
  });

  it("sends an unauthenticated visitor from a protected route to login", async () => {
    renderWithProviders(<App />, { route: "/projects" });

    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });

  it("returns the user to the cinematic entry on logout, not to a login form", async () => {
    useAuthStore.setState({ accessToken: "tok", refreshToken: "ref", user: null });
    const user = userEvent.setup();

    renderWithProviders(<App />, { route: "/projects" });

    await user.click((await screen.findAllByRole("button", { name: /log out/i }))[0]);

    // The public entry experience, not `/login` — signing out is a way
    // back to the front door, not a demand to sign in again.
    // Two calls to action on the entry page — the hero's and the
    // closing one — so this asserts on the set, not on a single node.
    expect((await screen.findAllByRole("link", { name: /enter aikdap/i })).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByRole("heading", { level: 1, name: "AIKDAP" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
  });

  it("still shows the entry at / after a reload following logout", async () => {
    /* A reload is a fresh mount reading the persisted auth store. The
     * route is chosen from whether a session exists, so there is no
     * "just logged out" flag that could go stale — clearing the session
     * is the whole mechanism. */
    useAuthStore.setState({ accessToken: null, refreshToken: null, user: null });

    renderWithProviders(<App />, { route: "/" });

    expect((await screen.findAllByRole("link", { name: /enter aikdap/i })).length).toBeGreaterThan(
      0,
    );
    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(true);
    });
  });

  it("takes the entry's call to action through to sign-in", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/" });

    await user.click(screen.getAllByRole("link", { name: /enter aikdap/i })[0]);

    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });
});
