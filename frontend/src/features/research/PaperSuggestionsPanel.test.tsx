import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PaperSuggestionsPanel, type SuggestedPaper } from "@/features/research/PaperSuggestionsPanel";
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
    const { container } = renderWithProviders(<PaperSuggestionsPanel papers={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a card per paper, with the publisher-link fallback when there is no OA PDF", () => {
    renderWithProviders(<PaperSuggestionsPanel papers={[makePaper()]} />);

    expect(screen.getByText("Deep Learning for Poultry Disease Detection")).toBeInTheDocument();
    expect(screen.getByText(/A\. Researcher, B\. Scientist/)).toBeInTheDocument();
    expect(screen.getByText(/2023/)).toBeInTheDocument();
    expect(screen.getByText(/42 citations/)).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "Open on publisher site" });
    expect(link).toHaveAttribute("href", "https://example.org/paper");
  });

  it("links to the open-access PDF when one is available", () => {
    renderWithProviders(
      <PaperSuggestionsPanel papers={[makePaper({ oa_pdf_url: "https://example.org/paper.pdf" })]} />,
    );

    const link = screen.getByRole("link", { name: "Open PDF" });
    expect(link).toHaveAttribute("href", "https://example.org/paper.pdf");
  });

  it("renders one card per paper when there are several", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", title: "First paper" }), makePaper({ openalex_id: "W2", title: "Second paper" })]}
      />,
    );

    expect(screen.getByText("First paper")).toBeInTheDocument();
    expect(screen.getByText("Second paper")).toBeInTheDocument();
  });
});
