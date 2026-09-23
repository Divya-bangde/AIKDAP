import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PaperMap, { paperUrl } from "@/features/paper-map/PaperMap";
import { toCanvasColor } from "@/features/paper-map/graph-colors";
import * as papersService from "@/services/papers";
import { renderWithProviders } from "@/test/render";

// Canvas can't render in jsdom: the stub lists nodes as buttons so a
// test can "click a node" the way ForceGraph's onNodeClick would.
vi.mock("react-force-graph-2d", () => ({
  default: ({
    graphData,
    onNodeClick,
  }: {
    graphData: { nodes: { id: string; title: string }[] };
    onNodeClick: (node: { id: string }) => void;
  }) => (
    <div>
      {graphData.nodes.map((node) => (
        <button key={node.id} type="button" onClick={() => onNodeClick(node)}>
          node {node.title}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("@/services/papers", () => ({ getPaperGraph: vi.fn(), importOutsidePaper: vi.fn() }));

const node = (id: string, kind: "uploaded" | "external", title: string) => ({
  id,
  title,
  year: 2020,
  cited_by_count: 12,
  kind,
  in_project: kind !== "external",
  doi: null,
  asset_id: null,
});

const connected = {
  nodes: [node("W1", "uploaded", "Paper one"), node("W2", "uploaded", "Paper two"), node("W9", "external", "Classic")],
  links: [
    { source: "W1", target: "W2" },
    { source: "W1", target: "W9" },
  ],
  unmatched: [{ paper_id: "a1", title: "draft.pdf", status: "not_found" }],
  external_unavailable: false,
};

beforeEach(() => {
  vi.mocked(papersService.getPaperGraph).mockReset();
  vi.mocked(papersService.importOutsidePaper).mockReset();
  // jsdom has no layout: report a width so the graph mounts.
  globalThis.ResizeObserver = class {
    constructor(private callback: ResizeObserverCallback) {}
    observe() {
      this.callback([{ contentRect: { width: 800 } } as ResizeObserverEntry], this as never);
    }
    disconnect() {}
    unobserve() {}
  } as unknown as typeof ResizeObserver;
});

describe("PaperMap", () => {
  it("explains the empty state and lists unmatched papers", async () => {
    vi.mocked(papersService.getPaperGraph).mockResolvedValue({
      nodes: [node("W1", "uploaded", "Only one")],
      links: [],
      unmatched: [{ paper_id: "a1", title: "draft.pdf", status: "pending" }],
      external_unavailable: false,
    });

    renderWithProviders(<PaperMap projectId="p1" />);

    expect(await screen.findByText("Not enough citations to map yet")).toBeInTheDocument();
    expect(screen.getByText(/Fewer than two of this project's PDFs are matched/)).toBeInTheDocument();
    expect(screen.getByText("draft.pdf")).toBeInTheDocument();
    expect(screen.getByText("Looking up")).toBeInTheDocument();
  });

  it("refetches with outside papers when the toggle is on", async () => {
    vi.mocked(papersService.getPaperGraph).mockResolvedValue(connected);
    const user = userEvent.setup();

    renderWithProviders(<PaperMap projectId="p1" />);
    await user.click(await screen.findByLabelText("Show frequently cited outside papers"));

    await waitFor(() => expect(papersService.getPaperGraph).toHaveBeenLastCalledWith("p1", true));
  });

  it("adds an outside paper from its panel", async () => {
    vi.mocked(papersService.getPaperGraph).mockResolvedValue(connected);
    vi.mocked(papersService.importOutsidePaper).mockResolvedValue({ openalex_id: "W9", status: "queued" });
    const user = userEvent.setup();

    renderWithProviders(<PaperMap projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "node Classic" }));

    const panel = await screen.findByRole("dialog");
    expect(panel).toHaveTextContent("Outside paper");
    expect(panel).toHaveTextContent("12");
    await user.click(screen.getByRole("button", { name: /Add to project/ }));

    expect(papersService.importOutsidePaper).toHaveBeenCalledWith("p1", "W9");
    expect(await screen.findByText(/joins the map as a project paper/)).toBeInTheDocument();
  });

  it("offers no add button for a project paper", async () => {
    vi.mocked(papersService.getPaperGraph).mockResolvedValue(connected);
    const user = userEvent.setup();

    renderWithProviders(<PaperMap projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "node Paper one" }));

    await screen.findByRole("dialog");
    expect(screen.queryByRole("button", { name: /Add to project/ })).not.toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("turns raw HSL channels into a canvas colour and keeps full colours", () => {
    expect(toCanvasColor("243 75% 59%")).toBe("hsl(243 75% 59%)");
    expect(toCanvasColor("oklch(0.7 0.1 250)")).toBe("oklch(0.7 0.1 250)");
    expect(toCanvasColor("#123456")).toBe("#123456");
    expect(toCanvasColor("")).toBe("#888");
  });

  it("links a paper by DOI when known, else by OpenAlex id", () => {
    expect(paperUrl({ id: "W1", doi: "10.1/x" })).toBe("https://doi.org/10.1/x");
    expect(paperUrl({ id: "W1", doi: null })).toBe("https://openalex.org/W1");
  });
});
