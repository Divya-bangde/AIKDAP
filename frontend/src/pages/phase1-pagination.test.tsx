import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Projects } from "@/pages/Projects";
import { Research } from "@/pages/Research";
import { Dashboard } from "@/pages/Dashboard";
import { AssetsLibrary } from "@/features/assets/AssetsLibrary";
import { makeAsset, makeProject, makeRun } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

const total = { projects: 301, assets: 503, documents: 401, runs: 702, grounded_answers: 601 };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("cursor history through the actual API clients", () => {
  it.each(["projects", "assets", "research runs"] as const)("reaches item 101 in %s without dropping previous pages", async (kind) => {
    const items = Array.from({ length: 101 }, (_, index) => {
      const id = `${kind}-${index + 1}`;
      if (kind === "projects") return makeProject({ id, name: `Project ${index + 1}` });
      if (kind === "assets") return makeAsset({ id, title: `Paper ${index + 1}` });
      return makeRun({ id, query: `Question ${index + 1}` });
    });
    const cursors: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/summary")) return json(total);
      if (url.pathname.endsWith("/page")) {
        const cursor = url.searchParams.get("cursor") ?? "0";
        cursors.push(cursor);
        const offset = Number(cursor);
        return json({ items: items.slice(offset, offset + 50), next_cursor: offset + 50 < items.length ? String(offset + 50) : null });
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    const user = userEvent.setup();
    renderWithProviders(kind === "projects" ? <Projects /> : kind === "assets" ? <AssetsLibrary projectId="p1" onOpenAnalytics={() => {}} /> : <Research />, { route: kind === "research runs" ? "/research?history=all" : "/" });
    const label = kind === "projects" ? "Project" : kind === "assets" ? "Paper" : "Question";
    expect(await screen.findByText(`${label} 1`)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Load more ${kind}` }));
    expect(await screen.findByText(`${label} 100`)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Load more ${kind}` }));
    expect(await screen.findByText(`${label} 101`)).toBeInTheDocument();
    expect(screen.getAllByText(`${label} 1`)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: `Load more ${kind}` })).not.toBeInTheDocument();
    expect(cursors).toEqual(["0", "50", "100"]);
  });

  it("retains loaded history on subsequent-page failure and retries the same cursor", async () => {
    let failed = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(String(input));
      if (!url.searchParams.has("cursor")) return json({ items: [makeRun({ query: "First question" })], next_cursor: "after-first" });
      expect(url.searchParams.get("cursor")).toBe("after-first");
      if (!failed) { failed = true; return json({ detail: "Temporary failure" }, 503); }
      return json({ items: [makeRun({ id: "r2", query: "Older question" })], next_cursor: null });
    });
    const user = userEvent.setup();
    renderWithProviders(<Research />, { route: "/research?history=all" });
    expect(await screen.findByText("First question")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more research runs" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load more research runs");
    expect(screen.getByText("First question")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry loading research runs" }));
    expect(await screen.findByText("Older question")).toBeInTheDocument();
  });

  it("uses aggregate totals beyond 100 independently of recent-page lengths", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/summary")) return json(total);
      if (url.pathname.endsWith("/projects/page")) return json({ items: [makeProject()], next_cursor: "older" });
      if (url.pathname.endsWith("/research/runs/page")) return json({ items: [makeRun()], next_cursor: "older" });
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    renderWithProviders(<Dashboard />);
    await waitFor(() => {
      for (const count of [301, 401, 702, 601]) expect(screen.getByText(String(count))).toBeInTheDocument();
    });
  });

  it("distinguishes unavailable recent data from an empty workspace", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "Unavailable" }, 503));
    renderWithProviders(<Dashboard />);
    expect(await screen.findAllByText(/Unavailable/)).not.toHaveLength(0);
    expect(screen.queryByText("No research projects yet.")).not.toBeInTheDocument();
    expect(screen.queryByText("Ask your first research question.")).not.toBeInTheDocument();
  });
});
