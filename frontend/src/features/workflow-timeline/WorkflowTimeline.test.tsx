import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowTimeline } from "@/features/workflow-timeline/WorkflowTimeline";
import { decisionLine, modelLabel } from "@/features/research/step-metrics";
import { mergeStep } from "@/hooks/useRunSteps";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

type Step = components["schemas"]["ResearchStepRead"];

const steps = vi.hoisted(() => ({
  listSteps: vi.fn(),
  createStepStreamToken: vi.fn(),
  openStepStream: vi.fn(),
}));
vi.mock("@/services/steps", () => steps);

function step(overrides: Partial<Step> = {}): Step {
  return {
    id: "s1",
    run_id: "r1",
    asset_id: null,
    attempt: 1,
    step_index: 0,
    node_name: "synthesis",
    title: "Synthesize the deliverable",
    status: "running",
    summary: null,
    output_payload: null,
    error_message: null,
    started_at: "2026-09-22T10:00:00Z",
    completed_at: null,
    duration_ms: null,
    created_at: "2026-09-22T10:00:00Z",
    model_provider: null,
    model_name: null,
    input_tokens: null,
    output_tokens: null,
    metadata: {},
    ...overrides,
  };
}

/** A controllable stand-in for the browser's EventSource. */
class FakeStream {
  listeners = new Map<string, (event: MessageEvent<string>) => void>();
  onerror: (() => void) | null = null;
  closed = false;
  addEventListener(name: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, listener);
  }
  emit(name: string, data: unknown) {
    this.listeners.get(name)?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  steps.createStepStreamToken.mockResolvedValue({ token: "t", expires_in: 60 });
});

describe("step helpers", () => {
  it("merges by id, keeps order, and never regresses a finished step", () => {
    const done = step({ status: "completed", completed_at: "x" });
    const merged = mergeStep([done], step({ id: "s0", step_index: -1 }));
    expect(merged.map((s) => s.id)).toEqual(["s0", "s1"]);
    // An older, still-running copy (a lagging poll) does not undo "completed".
    expect(mergeStep([done], step())[0].status).toBe("completed");
  });

  it("phrases the KB-vs-web decision in plain language", () => {
    expect(
      decisionLine(step({ metadata: { used_web: false, reason: "Found enough in your documents." } })),
    ).toBe("Web search: not used. Found enough in your documents.");
    expect(decisionLine(step())).toBeUndefined();
    expect(modelLabel(step({ model_name: "groq/llama-3.1-8b" }))).toBe("llama-3.1-8b");
  });
});

describe("WorkflowTimeline", () => {
  it("merges live stream events into the timeline and closes on end", async () => {
    const stream = new FakeStream();
    steps.openStepStream.mockReturnValue(stream);
    renderWithProviders(
      <WorkflowTimeline owner={{ kind: "run", id: "r1" }} active seed={[step()]} />,
    );
    await waitFor(() => expect(steps.openStepStream).toHaveBeenCalledWith({ kind: "run", id: "r1" }, "t"));

    act(() =>
      stream.emit(
        "step",
        step({
          status: "completed",
          completed_at: "x",
          duration_ms: 42,
          model_name: "groq/llama-3.1-8b",
          metadata: { used_web: false, reason: "Found enough in your documents." },
        }),
      ),
    );
    expect(await screen.findByText("completed")).toBeInTheDocument();
    expect(screen.getByText("llama-3.1-8b")).toBeInTheDocument();
    expect(screen.getByText("Web search: not used. Found enough in your documents.")).toBeInTheDocument();

    act(() => stream.emit("end", { reason: "terminal" }));
    expect(stream.closed).toBe(true);
    // The seed means the caller already polls: no separate list request.
    expect(steps.listSteps).not.toHaveBeenCalled();
  });

  it("shows a failed step's error", async () => {
    steps.openStepStream.mockReturnValue(new FakeStream());
    renderWithProviders(
      <WorkflowTimeline
        owner={{ kind: "run", id: "r1" }}
        active={false}
        seed={[step({ status: "failed", completed_at: "x", error_message: "RuntimeError: boom" })]}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("RuntimeError: boom");
    expect(steps.createStepStreamToken).not.toHaveBeenCalled(); // terminal: no stream
  });

  it("loads the list itself and falls back to polling when the stream is unavailable", async () => {
    steps.openStepStream.mockReturnValue(null);
    steps.listSteps.mockResolvedValue([step({ node_name: "planner" })]);
    renderWithProviders(<WorkflowTimeline owner={{ kind: "report", id: "a1" }} active />);
    await waitFor(() => expect(steps.listSteps).toHaveBeenCalledTimes(2), { timeout: 4000 });
    expect(steps.listSteps).toHaveBeenCalledWith({ kind: "report", id: "a1" });
  });
});
