import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { presentationFor } from "@/features/research/research-presentation";

import { ResearchPipeline } from "@/features/research/ResearchPipeline";
import type { components } from "@/types/api";

type ResearchStepRead = components["schemas"]["ResearchStepRead"];

function makeStep(overrides: Partial<ResearchStepRead>): ResearchStepRead {
  return {
    id: crypto.randomUUID(),
    run_id: "run-1",
    asset_id: null,
    attempt: 1,
    step_index: 0,
    node_name: "planner",
    title: "Plan the research run",
    status: "pending",
    summary: null,
    output_payload: null,
    error_message: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("ResearchPipeline", () => {
  it("renders each step's real backend status verbatim", () => {
    render(
      <ResearchPipeline
        steps={[
          makeStep({ step_index: 0, title: "Plan", status: "completed" }),
          makeStep({ step_index: 1, title: "Retrieve", status: "running" }),
          makeStep({ step_index: 2, title: "Web", status: "skipped" }),
          makeStep({ step_index: 3, title: "Synthesize", status: "pending" }),
        ]}
      />,
    );

    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    // A skipped node is never dressed up as completed.
    expect(screen.getByText("skipped")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("orders steps by the backend's step_index, not array order", () => {
    // Sprint 9K.8: the primary text is now the plain-language title
    // derived from `node_name` (Phase 2), not the raw backend `title`
    // string — so ordering is proven with three distinct real node
    // names rather than three distinct custom titles.
    render(
      <ResearchPipeline
        steps={[
          makeStep({ step_index: 2, node_name: "synthesis", title: "Third stage" }),
          makeStep({ step_index: 0, node_name: "planner", title: "First stage" }),
          makeStep({ step_index: 1, node_name: "asset_retrieval", title: "Second stage" }),
        ]}
      />,
    );

    const expected = [
      presentationFor("planner").title,
      presentationFor("asset_retrieval").title,
      presentationFor("synthesis").title,
    ];
    const titles = screen.getAllByText((_, el) => expected.includes(el?.textContent ?? ""));
    expect(titles.map((el) => el.textContent)).toEqual(expected);
  });

  it("shows the plain-language presentation for a real node type, not the raw backend title", () => {
    render(
      <ResearchPipeline
        steps={[
          makeStep({
            node_name: "asset_retrieval",
            title: "Search the project knowledge base",
            status: "completed",
          }),
        ]}
      />,
    );

    expect(screen.getByText("Searching your knowledge base")).toBeInTheDocument();
    expect(
      screen.getByText("Finding relevant information from your uploaded documents."),
    ).toBeInTheDocument();
    // The raw backend title still exists — inside the collapsed
    // technical detail, not as the primary text (see the disclosure
    // tests below).
    expect(screen.queryByText("Search the project knowledge base")).not.toBeInTheDocument();
  });

  it("keeps technical detail collapsed by default", () => {
    render(
      <ResearchPipeline
        steps={[
          makeStep({
            node_name: "asset_retrieval",
            status: "completed",
            output_payload: { document_count: 3, reranking_status: "completed" },
          }),
        ]}
      />,
    );

    const toggle = screen.getByRole("button", { name: "Technical details" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Chunks retrieved")).not.toBeInTheDocument();
    expect(screen.queryByText("3")).not.toBeInTheDocument();
  });

  it("reveals only metrics the backend actually supplied once expanded, and nothing invented", async () => {
    const user = userEvent.setup();
    render(
      <ResearchPipeline
        steps={[
          makeStep({
            node_name: "asset_retrieval",
            title: "Search the project knowledge base",
            status: "completed",
            output_payload: { document_count: 3, reranking_status: "completed" },
          }),
        ]}
      />,
    );

    const toggle = screen.getByRole("button", { name: "Technical details" });
    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Chunks retrieved")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Reranking")).toBeInTheDocument();
    // The raw backend title is available here, in the technical layer.
    expect(screen.getByText(/Search the project knowledge base/)).toBeInTheDocument();
    // Nothing invented for keys this payload never contained.
    expect(screen.queryByText("Evidence supplied")).not.toBeInTheDocument();
    expect(screen.queryByText("Latency")).not.toBeInTheDocument();

    // Collapses again on a second click — real toggle, not one-shot
    // reveal. The state flips synchronously; the content's removal
    // from the DOM waits on Motion's exit animation finishing.
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryByText("Chunks retrieved")).not.toBeInTheDocument());
  });

  it("is keyboard operable and exposes a working aria-controls target", async () => {
    const user = userEvent.setup();
    render(
      <ResearchPipeline
        steps={[
          makeStep({
            node_name: "synthesis",
            status: "completed",
            output_payload: { provider: "gemini", model: "gemini-flash-latest" },
          }),
        ]}
      />,
    );

    const toggle = screen.getByRole("button", { name: "Technical details" });
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const controlsId = toggle.getAttribute("aria-controls");
    expect(controlsId).toBeTruthy();
    expect(document.getElementById(controlsId!)).not.toBeNull();

    await user.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("honestly says nothing was recorded rather than showing an empty technical panel", async () => {
    const user = userEvent.setup();
    render(<ResearchPipeline steps={[makeStep({ node_name: "router", status: "completed" })]} />);

    await user.click(screen.getByRole("button", { name: "Technical details" }));

    expect(
      screen.getByText("No additional technical detail was recorded for this step."),
    ).toBeInTheDocument();
  });

  it("surfaces a failed step's real error message in an alert", () => {
    render(
      <ResearchPipeline
        steps={[
          makeStep({
            node_name: "synthesis",
            title: "Synthesize the deliverable",
            status: "failed",
            error_message: "LLMQuotaExhaustedError: gemini was not called.",
          }),
        ]}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "LLMQuotaExhaustedError: gemini was not called.",
    );
  });

  it("waits for the backend rather than inventing stages when no steps exist yet", () => {
    render(<ResearchPipeline steps={[]} />);

    expect(screen.getByText(/Waiting for the pipeline to start/)).toBeInTheDocument();
    expect(screen.queryByText("completed")).not.toBeInTheDocument();
  });
});
