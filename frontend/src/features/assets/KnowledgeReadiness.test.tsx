import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeReadiness } from "@/features/assets/KnowledgeReadiness";
import * as knowledge from "@/services/knowledge";
import { renderWithProviders } from "@/test/render";

vi.mock("@/services/knowledge");
const initial: knowledge.KnowledgeReadiness = {
  project_id: "p1", configured_space: { id: "jina-space", provider: "jina_ai", model: "jina-v3", version: "v1", dimensions: 1024, document_task: null, query_task: null },
  active_space_id: "bge-space", total_chunks: 120, searchable_chunks: 0, legacy_chunks: 120,
  ready: false, compatible: false, rebuild: null,
};
beforeEach(() => vi.clearAllMocks());

describe("embedding readiness", () => {
  it("keeps queued work disabled and polls until validated coverage is ready", async () => {
    vi.mocked(knowledge.getKnowledgeReadiness).mockResolvedValueOnce(initial).mockResolvedValue({
      ...initial, active_space_id: "jina-space", compatible: true, ready: true,
      searchable_chunks: 120, legacy_chunks: 0, rebuild: { id: "r1", status: "completed", total: 120, completed: 120, failed: 0, error: null },
    });
    vi.mocked(knowledge.rebuildKnowledge).mockResolvedValue({ ...initial, rebuild: {
      id: "r1", status: "queued", total: 120, completed: 0, failed: 0, error: null,
    } });
    const user = userEvent.setup();
    renderWithProviders(<KnowledgeReadiness projectId="p1" />);
    await user.click(await screen.findByRole("button", { name: "Rebuild embeddings" }));
    expect(await screen.findByRole("button", { name: "Rebuilding…" })).toBeDisabled();
    expect(screen.getByText(/Rebuild queued/)).toBeInTheDocument();
    expect(await screen.findByText("Knowledge base ready", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText("120 of 120 passages searchable in the configured embedding space.")).toBeInTheDocument();
    expect(knowledge.rebuildKnowledge).toHaveBeenCalledTimes(1);
  });

  it("shows dispatch failure and permits explicit retry for a saved failed job", async () => {
    vi.mocked(knowledge.getKnowledgeReadiness).mockResolvedValue({ ...initial, rebuild: {
      id: "r1", status: "failed", total: 120, completed: 32, failed: 1, error: "Dispatch unavailable; retry",
    } });
    vi.mocked(knowledge.rebuildKnowledge).mockResolvedValue({ ...initial, rebuild: { id: "r1", status: "queued", total: 120, completed: 32, failed: 0, error: null } });
    renderWithProviders(<KnowledgeReadiness projectId="p1" />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Resume rebuild" }));
    await waitFor(() => expect(knowledge.rebuildKnowledge).toHaveBeenCalledWith("p1"));
  });
});
