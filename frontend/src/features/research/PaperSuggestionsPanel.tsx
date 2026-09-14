import { ExternalLink } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** One OpenAlex work `paper_suggestion_node` suggested for this run.
 * Mirrors the backend's `paper_suggestion.SuggestedPaper` exactly --
 * see `models.ResearchRun.suggested_papers`. */
export interface SuggestedPaper {
  openalex_id: string;
  title: string;
  authors: string[];
  year: number | null;
  cited_by_count: number;
  landing_url: string;
  oa_pdf_url: string | null;
  relevance_note: string;
}

/** "These papers could strengthen this answer" (spec section 2). Shown
 * only when the run actually has suggestions -- an empty gap search is
 * not a failure worth a card of its own, it is simply nothing to show.
 * No "Add" action yet: that is step 3 (Add & re-run). */
export function PaperSuggestionsPanel({ papers }: { papers: SuggestedPaper[] }) {
  if (papers.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Strengthen this answer</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          These papers could strengthen this answer. Add them to your project.
        </p>
        <ul className="flex flex-col gap-2">
          {papers.map((paper) => (
            <li key={paper.openalex_id} className="flex flex-col gap-1.5 rounded-lg bg-sunken p-3">
              <span className="text-sm font-medium text-foreground">{paper.title}</span>
              <span className="text-xs text-muted-foreground">
                {paper.authors.length > 0 ? paper.authors.join(", ") : "Unknown authors"}
                {paper.year ? ` · ${paper.year}` : ""}
                {" · "}
                {paper.cited_by_count} citation{paper.cited_by_count === 1 ? "" : "s"}
              </span>
              <a
                href={paper.oa_pdf_url ?? paper.landing_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
              >
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
                {paper.oa_pdf_url ? "Open PDF" : "Open on publisher site"}
              </a>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
