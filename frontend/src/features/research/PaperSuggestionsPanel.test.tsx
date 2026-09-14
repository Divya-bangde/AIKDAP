import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaperSuggestionsPanel, type SuggestedPaper } from "@/features/research/PaperSuggestionsPanel";
import * as researchService from "@/services/research";
import { renderWithProviders } from "@/test/render";

function makePaper(overrides: Partial<SuggestedPaper> = {}): SuggestedPaper {
  return {
    openalex_id: "https://openalex.org/W1",
    title: "Deep Learning for Poultry Disease Detection",
    authors: ["A. Researcher", "B. Scientist"],
    year: 2023,
    cited_by_count: 42,
    landing_url: "https://example.org/paper",
    oa_pdf_url: null,
    relevance_note: "Suggested to help address: missing baseline comparison.",
    ...overrides,
  };
}

describe("PaperSuggestionsPanel", () => {
  it("renders nothing when there are no papers", () => {
    const { container } = renderWithProviders(<PaperSuggestionsPanel papers={[]} runId="run-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a card per paper, with the publisher-link fallback when there is no OA PDF", () => {
    renderWithProviders(<PaperSuggestionsPanel papers={[makePaper()]} runId="run-1" />);

    expect(screen.getByText("Deep Learning for Poultry Disease Detection")).toBeInTheDocument();
    expect(screen.getByText(/A\. Researcher, B\. Scientist/)).toBeInTheDocument();
    expect(screen.getByText(/2023/)).toBeInTheDocument();
    expect(screen.getByText(/42 citations/)).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "Open on publisher site" });
    expect(link).toHaveAttribute("href", "https://example.org/paper");
  });

  it("links to the open-access PDF when one is available", () => {
    renderWithProviders(
      <PaperSuggestionsPanel papers={[makePaper({ oa_pdf_url: "https://example.org/paper.pdf" })]} runId="run-1" />,
    );

    const link = screen.getByRole("link", { name: "Open PDF" });
    expect(link).toHaveAttribute("href", "https://example.org/paper.pdf");
  });

  it("renders one card per paper when there are several", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", title: "First paper" }), makePaper({ openalex_id: "W2", title: "Second paper" })]}
        runId="run-1"
      />,
    );

    expect(screen.getByText("First paper")).toBeInTheDocument();
    expect(screen.getByText("Second paper")).toBeInTheDocument();
  });

  it("gives a paper with an OA PDF a checkbox, and a paywalled paper none", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[
          makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf" }),
          makePaper({ openalex_id: "W2", oa_pdf_url: null }),
        ]}
        runId="run-1"
      />,
    );

    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  });

  it("keeps the Add & re-run button disabled until a paper is selected", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf" })]}
        runId="run-1"
      />,
    );

    const button = screen.getByRole("button", { name: /add & re-run/i });
    expect(button).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
  });

  it("calls importSuggestedPapers with the selected ids and notifies the parent on success", async () => {
    const importSpy = vi
      .spyOn(researchService, "importSuggestedPapers")
      .mockResolvedValue({ run_id: "run-1", papers: [{ openalex_id: "W1", status: "queued" }] });
    const onImported = vi.fn();

    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf" })]}
        runId="run-1"
        onImported={onImported}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /add & re-run/i }));

    await waitFor(() => expect(importSpy).toHaveBeenCalledWith("run-1", ["W1"]));
    await waitFor(() => expect(onImported).toHaveBeenCalled());
  });

  it("renders each card's import status once present", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[
          makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf", import_status: "processing" }),
          makePaper({
            openalex_id: "W2",
            oa_pdf_url: "https://example.org/b.pdf",
            import_status: "failed",
            import_error: "Downloaded content is not a PDF.",
          }),
        ]}
        runId="run-1"
      />,
    );

    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Downloaded content is not a PDF.")).toBeInTheDocument();
  });

  it("links to the re-run once it exists", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[
          makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf", import_status: "added" }),
        ]}
        runId="run-1"
        rerunRunId="run-2"
      />,
    );

    const link = screen.getByRole("link", { name: /view re-run/i });
    expect(link).toHaveAttribute("href", "/research/run-2");
  });
});
