import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ResearchResult } from "@/features/research/ResearchResult";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

// `EvidenceGapPanel` (Sprint 16 Phase 8.11 Part C) fetches assets and
// calls the document-analysis endpoint whenever the insufficient-
// evidence branch renders -- mocked here so these tests exercise
// `ResearchResult`'s own rendering decisions, not a real network call.
// An unresolved promise is enough: these tests only assert on content
// `ResearchResult` itself renders synchronously.
vi.mock("@/services/assets", () => ({ listAssets: vi.fn(() => new Promise(() => {})) }));
vi.mock("@/services/research", () => ({
  analyzeResearchDocument: vi.fn(() => new Promise(() => {})),
  createUnsourcedAnswer: vi.fn(() => new Promise(() => {})),
}));

const render = renderWithProviders;

type ResearchRunDetail = components["schemas"]["ResearchRunDetail"];

/** Matches an element whose full rendered text is exactly `expected`,
 * regardless of how many child elements it is split across. Sprint
 * 9K.2 wraps the numeric part of these lines in its own element for
 * tabular figures, which breaks a plain string matcher without
 * changing a single rendered character. */
function wholeText(expected: string) {
  return (_content: string, element: Element | null) =>
    element?.textContent?.replace(/\s+/g, " ").trim() === expected;
}

function makeRun(overrides: Partial<ResearchRunDetail>): ResearchRunDetail {
  return {
    id: "run-1",
    project_id: "project-1",
    owner_id: "owner-1",
    task_id: null,
    query: "What challenges does ABC Poultry face?",
    status: "completed",
    include_assets: true,
    include_web: false,
    max_results: 5,
    objective: null,
    plan: null,
    final_answer: null,
    citations: [],
    grounding_status: null,
    error_message: null,
    celery_task_id: null,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    duration_ms: 1200,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    steps: [],
    ...overrides,
  };
}

describe("ResearchResult", () => {
  it("shows Insufficient Evidence, zero evidence, and no fabricated answer when the backend says so", () => {
    const run = makeRun({
      grounding_status: "insufficient_evidence",
      final_answer: null,
      citations: [],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText("Insufficient Evidence")).toBeInTheDocument();
    expect(screen.getByText(/did not contain enough relevant evidence/i)).toBeInTheDocument();
    expect(screen.getAllByText(wholeText("Evidence found: 0")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(wholeText("Providers used: None")).length).toBeGreaterThan(0);
    // Never renders the grounded success card for this state.
    expect(screen.queryByText("Grounded Intelligence")).not.toBeInTheDocument();
  });

  it("renders a grounded answer with the exact grounding badge and citations", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "ABC Poultry faces feed cost inflation and biosecurity risk.",
      citations: [
        {
          id: "c1",
          title: "ABC Poultry FY2026 Review",
          source: "asset",
          snippet: "Feed costs rose 18% year-over-year.",
          retrieval_rank: 1,
          rerank_score: 6.15,
          simulated: false,
        },
      ],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText("Grounded")).toBeInTheDocument();
    // Anchors the negative checks elsewhere: this title must actually render.
    expect(screen.getByText("Grounded Intelligence")).toBeInTheDocument();
    expect(
      screen.getByText("ABC Poultry faces feed cost inflation and biosecurity risk."),
    ).toBeInTheDocument();
    // Sprint 9K.2 replaced the always-visible evidence panel with an
    // on-demand drawer, so the citation title now renders once in the
    // sources list rather than twice. The full evidence record is
    // still reachable — see the drawer tests below.
    expect(screen.getByText(/ABC Poultry FY2026 Review/)).toBeInTheDocument();
    expect(screen.getByText("Rerank score: 6.15")).toBeInTheDocument();
    expect(screen.queryByText("Simulated evidence")).not.toBeInTheDocument();
  });

  it("renders the Partially Grounded badge distinctly from Grounded", () => {
    const run = makeRun({
      grounding_status: "partially_grounded",
      final_answer: "Some of this is supported.",
      citations: [{ id: "c1", title: "Doc", simulated: false }],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText("Partially Grounded")).toBeInTheDocument();
    expect(screen.queryByText("Grounded")).not.toBeInTheDocument();
  });

  it("flags simulated evidence explicitly rather than hiding it", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer using simulated web evidence.",
      citations: [
        {
          id: "c1",
          title: "Simulated external reference",
          source: "web",
          simulated: true,
        },
      ],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText("Simulated evidence")).toBeInTheDocument();
  });

  it("never displays a rerank score as a percentage or probability", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer.",
      citations: [{ id: "c1", title: "Doc", rerank_score: 6.15 }],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText("Rerank score: 6.15")).toBeInTheDocument();
    expect(screen.queryByText(/6\.15%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
  });

  it("keeps provider/model/fallback detail collapsed by default (Sprint 9K.8, Phase 8)", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer via fallback.",
      citations: [{ id: "c1", title: "Doc" }],
      steps: [
        {
          id: "step-1",
          run_id: "run-1",
          asset_id: null,
          attempt: 1,
          step_index: 5,
          node_name: "synthesis",
          title: "Synthesize the deliverable",
          status: "completed",
          summary: null,
          output_payload: {
            provider: "groq",
            model: "openai/gpt-oss-120b",
            fallback_used: true,
            primary_error_type: "LLMQuotaExhaustedError",
          },
          error_message: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          created_at: new Date().toISOString(),
        },
      ],
    });

    render(<ResearchResult run={run} />);

    // Provider/model no longer sit inline on the main answer card —
    // they live behind the same technical disclosure the pipeline
    // steps use, so an executive reading the answer isn't stopped by
    // "gemini-flash-latest" and "Fallback used: No" before the point.
    expect(screen.queryByText("groq")).not.toBeInTheDocument();
    expect(screen.queryByText(/Fallback used:/)).not.toBeInTheDocument();
  });

  it("shows provider, model and a real fallback narrative once technical details are expanded", async () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer via fallback.",
      citations: [{ id: "c1", title: "Doc" }],
      steps: [
        {
          id: "step-1",
          run_id: "run-1",
          asset_id: null,
          attempt: 1,
          step_index: 5,
          node_name: "synthesis",
          title: "Synthesize the deliverable",
          status: "completed",
          summary: null,
          output_payload: {
            provider: "groq",
            model: "openai/gpt-oss-120b",
            fallback_used: true,
            primary_error_type: "LLMQuotaExhaustedError",
          },
          error_message: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          created_at: new Date().toISOString(),
        },
      ],
    });
    const user = userEvent.setup();

    render(<ResearchResult run={run} />);
    await user.click(screen.getByRole("button", { name: "Technical details" }));

    expect(screen.getByText("groq")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-oss-120b")).toBeInTheDocument();
    expect(screen.getByText(/Fallback used:/)).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    // The narrative is built only from real fields the backend
    // returned — never a claim the run itself didn't make.
    expect(screen.getByText(/Primary provider was unavailable/)).toHaveTextContent(
      "LLMQuotaExhaustedError",
    );
  });

  it("never claims a fallback narrative unless the backend actually reports fallback_used", async () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer, no fallback.",
      citations: [{ id: "c1", title: "Doc" }],
      steps: [
        {
          id: "step-1",
          run_id: "run-1",
          asset_id: null,
          attempt: 1,
          step_index: 5,
          node_name: "synthesis",
          title: "Synthesize the deliverable",
          status: "completed",
          summary: null,
          output_payload: { provider: "gemini", model: "gemini-flash-latest", fallback_used: false },
          error_message: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          created_at: new Date().toISOString(),
        },
      ],
    });
    const user = userEvent.setup();

    render(<ResearchResult run={run} />);
    await user.click(screen.getByRole("button", { name: "Technical details" }));

    expect(screen.getByText("gemini")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
    expect(screen.queryByText(/Primary provider was unavailable/)).not.toBeInTheDocument();
  });

  it("says how much evidence supports a grounded answer, using the real citation count", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer.",
      citations: [{ id: "c1", title: "Doc" }, { id: "c2", title: "Doc 2" }],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText(/Supported by 2 evidence items/)).toBeInTheDocument();
  });

  it("never claims evidence support when there are no citations", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer with no citations returned.",
      citations: [],
    });

    render(<ResearchResult run={run} />);

    expect(screen.queryByText(/Supported by/)).not.toBeInTheDocument();
  });

  it("opens the evidence drawer with the citation's real retrieval detail", async () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Feed costs rose sharply.",
      citations: [
        {
          id: "c1",
          title: "ABC Poultry FY2026 Review",
          snippet: "Feed costs rose 18% year-over-year.",
          retrieval_rank: 1,
          retrieval_score: 0.638,
          rerank_score: 4.89,
          chunk_id: "chunk-abc",
          reranking_status: "completed",
        },
      ],
    });
    const user = userEvent.setup();

    render(<ResearchResult run={run} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /ABC Poultry FY2026 Review/ }));

    const drawer = await screen.findByRole("dialog");
    // Both stage scores stay distinct and unconverted inside the drawer.
    expect(drawer).toHaveTextContent("0.638");
    expect(drawer).toHaveTextContent("4.89");
    expect(drawer).toHaveTextContent("chunk-abc");
    // Neither score is ever converted into a percentage or relabelled
    // as confidence. (A bare "%" check would be wrong here: the real
    // snippet text itself contains "18%".)
    const text = drawer.textContent ?? "";
    expect(text).not.toMatch(/0\.638\s*%|63\.8\s*%|4\.89\s*%|489\s*%/);
    expect(text).not.toMatch(/confidence/i);
  });

  it("does not make up a citation for a marker the backend never returned", () => {
    const run = makeRun({
      grounding_status: "grounded",
      // `[c9]` has no matching citation; `[c1]` does.
      final_answer: "Supported claim [c1]. Unsupported marker [c9].",
      citations: [{ id: "c1", title: "Real source" }],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByRole("button", { name: "View evidence for citation c1" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "View evidence for citation c9" }),
    ).not.toBeInTheDocument();
    // The unmatched marker is still shown as written, not deleted.
    expect(screen.getByText(/\[c9\]/)).toBeInTheDocument();
  });

  it("builds the evidence funnel only from fields the backend actually returned", () => {
    const run = makeRun({
      grounding_status: "grounded",
      final_answer: "Answer.",
      citations: [{ id: "c1", title: "Doc", relevance_threshold: -2 }],
      steps: [
        {
          id: "s1",
          run_id: "run-1",
          asset_id: null,
          attempt: 1,
          step_index: 2,
          node_name: "asset_retrieval",
          title: "Search the project knowledge base",
          status: "completed",
          summary: null,
          output_payload: { document_count: 1, reranking_status: "completed" },
          error_message: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          created_at: new Date().toISOString(),
        },
        {
          id: "s2",
          run_id: "run-1",
          asset_id: null,
          attempt: 1,
          step_index: 4,
          node_name: "context_builder",
          title: "Build the working context",
          status: "completed",
          summary: null,
          output_payload: { received: 6, included: 6 },
          error_message: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          created_at: new Date().toISOString(),
        },
      ],
    });

    render(<ResearchResult run={run} />);

    expect(screen.getByText("Evidence funnel")).toBeInTheDocument();
    expect(screen.getByText("Retrieved")).toBeInTheDocument();
    expect(screen.getByText("In context")).toBeInTheDocument();
    expect(screen.getByText("of 6 received")).toBeInTheDocument();
    // No synthesis step was supplied, so those stages must be absent
    // rather than rendered as zero.
    expect(screen.queryByText("Evidence supplied")).not.toBeInTheDocument();
    expect(screen.queryByText("Cited")).not.toBeInTheDocument();
    // The threshold is shown because a citation actually carried one.
    expect(screen.getByText("Relevance threshold")).toBeInTheDocument();
  });

  it("shows the Experiment Playground only when the run carries equations", () => {
    const base = { grounding_status: "grounded" as const, final_answer: "Answer.", citations: [] };
    const { unmount } = render(<ResearchResult run={makeRun(base)} />);
    expect(screen.queryByText("Experiment Playground")).not.toBeInTheDocument();
    unmount();

    render(
      <ResearchResult
        run={makeRun({ ...base, equations: [{ label: "Schwarzschild radius", latex: "r_s = \\frac{2GM}{c^2}", expression: "r_s = 2*G*M/c**2" }] })}
      />,
    );
    expect(screen.getByText("Experiment Playground")).toBeInTheDocument();
    expect(screen.getByText("Schwarzschild radius")).toBeInTheDocument();
  });

  describe("general-knowledge (unsourced) answers", () => {
    const synthesisStep = (outputPayload: Record<string, unknown>) => ({
      id: "step-s",
      run_id: "run-1",
      asset_id: null,
      attempt: 1,
      step_index: 5,
      node_name: "synthesis",
      title: "Synthesize the deliverable",
      status: "completed" as const,
      summary: null,
      output_payload: outputPayload,
      error_message: null,
      started_at: null,
      completed_at: null,
      duration_ms: null,
      created_at: new Date().toISOString(),
    });

    it("labels the answer as general knowledge and never claims evidence support", () => {
      const run = makeRun({
        grounding_status: "unsourced",
        final_answer:
          "**This could not be grounded in your project's documents or web search. Answered briefly from general knowledge — not from your documents.**\n\nDeep learning is a subset of machine learning.",
        citations: [],
        steps: [
          synthesisStep({
            grounding_status: "unsourced",
            topic_relation: "related",
            general_knowledge_used: true,
          }),
        ],
      });

      render(<ResearchResult run={run} />);

      expect(screen.getByText("From general knowledge — not your documents")).toBeInTheDocument();
      expect(screen.getByText(/Deep learning is a subset of machine learning/)).toBeInTheDocument();
      expect(screen.getByText("Unsourced")).toBeInTheDocument();
      expect(screen.queryByText("Grounded Intelligence")).not.toBeInTheDocument();
      expect(screen.queryByText(/Supported by/)).not.toBeInTheDocument();
      expect(screen.queryByText("Evidence funnel")).not.toBeInTheDocument();
      expect(screen.queryByText("No relevant evidence was found.")).not.toBeInTheDocument();
      expect(screen.queryByText("Off topic")).not.toBeInTheDocument();
    });

    it("adds an Off topic badge when the synthesis step says the question was off topic", () => {
      const run = makeRun({
        grounding_status: "unsourced",
        final_answer:
          "**This question is outside the topic of your project's documents. Answered briefly from general knowledge — not from your documents.**\n\nParis is the capital of France.",
        citations: [],
        steps: [
          synthesisStep({
            grounding_status: "unsourced",
            topic_relation: "off_topic",
            general_knowledge_used: true,
          }),
        ],
      });

      render(<ResearchResult run={run} />);

      expect(screen.getByText("Off topic")).toBeInTheDocument();
      expect(screen.getByText("From general knowledge — not your documents")).toBeInTheDocument();
    });
  });
});
