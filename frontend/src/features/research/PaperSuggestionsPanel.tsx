import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { messageFor } from "@/lib/api-error";
import * as researchService from "@/services/research";

/** One OpenAlex work `paper_suggestion_node` suggested for this run.
 * Mirrors the backend's `paper_suggestion.SuggestedPaper` exactly --
 * see `models.ResearchRun.suggested_papers`. The `import_*` fields are
 * written by Milestone 10 step 3's import chord directly onto this
 * same JSONB entry -- absent until an import has been requested. */
export interface SuggestedPaper {
  openalex_id: string;
  title: string;
  authors: string[];
  year: number | null;
  cited_by_count: number;
  landing_url: string;
  oa_pdf_url: string | null;
  relevance_note: string;
  import_status?: "queued" | "processing" | "added" | "failed";
  imported_asset_id?: string | null;
  import_error?: string | null;
}

/** "These papers could strengthen this answer" (spec section 2), with
 * "Add & re-run" (spec section 3): a paper with an open-access PDF gets
 * a checkbox; a paywalled one keeps only the publisher link. Once at
 * least one is selected, "Add & re-run" imports the selected papers as
 * project assets and -- once every one reaches a final state -- starts
 * one new research run linked back to this one. `onImported` lets the
 * caller (the polling `ResearchRunView`) refetch immediately rather
 * than waiting for the next poll tick. */
export function PaperSuggestionsPanel({
  papers,
  runId,
  onImported,
  rerunRunId,
}: {
  papers: SuggestedPaper[];
  runId: string;
  onImported?: () => void;
  rerunRunId?: string | null;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const mutation = useMutation({
    mutationFn: (openalexIds: string[]) => researchService.importSuggestedPapers(runId, openalexIds),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["research", "run", runId] });
      onImported?.();
    },
  });

  if (papers.length === 0) return null;

  function toggle(openalexId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(openalexId)) next.delete(openalexId);
      else next.add(openalexId);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle>Strengthen this answer</CardTitle>
        {rerunRunId && (
          <Link to={`/research/${rerunRunId}`} className="text-xs font-medium text-primary hover:underline">
            View re-run &rarr;
          </Link>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          These papers could strengthen this answer. Add them to your project.
        </p>
        <ul className="flex flex-col gap-2">
          {papers.map((paper) => (
            <li key={paper.openalex_id} className="flex flex-col gap-1.5 rounded-lg bg-sunken p-3">
              <div className="flex items-start gap-2.5">
                {paper.oa_pdf_url && (
                  <input
                    type="checkbox"
                    aria-label={`Select ${paper.title}`}
                    checked={selected.has(paper.openalex_id)}
                    onChange={() => toggle(paper.openalex_id)}
                    className="mt-0.5 h-4 w-4 shrink-0"
                  />
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{paper.title}</span>
                    {paper.import_status && <StatusBadge domain="paperImport" value={paper.import_status} />}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {paper.authors.length > 0 ? paper.authors.join(", ") : "Unknown authors"}
                    {paper.year ? ` · ${paper.year}` : ""}
                    {" · "}
                    {paper.cited_by_count} citation{paper.cited_by_count === 1 ? "" : "s"}
                  </span>
                  {paper.import_status === "failed" && paper.import_error && (
                    <p className="text-xs text-destructive">{paper.import_error}</p>
                  )}
                  <a
                    href={paper.oa_pdf_url ?? paper.landing_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-fit items-center gap-1 text-xs font-medium text-primary hover:underline"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    {paper.oa_pdf_url ? "Open PDF" : "Open on publisher site"}
                  </a>
                </div>
              </div>
            </li>
          ))}
        </ul>

        {mutation.isError && (
          <p role="alert" className="text-sm text-destructive">
            {messageFor(mutation.error)}
          </p>
        )}

        <div className="flex justify-end">
          <Button
            type="button"
            disabled={selected.size === 0 || mutation.isPending}
            onClick={() => mutation.mutate(Array.from(selected))}
          >
            {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Add &amp; re-run
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
