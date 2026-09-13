import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, FileSearch, Lightbulb } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/EmptyState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { UploadDropzone } from "@/features/assets/UploadDropzone";
import { AnswerBody } from "@/features/research/AnswerBody";
import * as assetsService from "@/services/assets";
import { analyzeResearchDocument, createUnsourcedAnswer } from "@/services/research";
import type { components } from "@/types/api";

type ResearchGap = components["schemas"]["ResearchGap"];

/** Surfaces the real reason a run declined to answer, at the exact
 * moment a reader hits that wall (Sprint 16 Phase 8.11 Part C).
 *
 * `analysis.py`'s gap classification (`ResearchGap`,
 * `sufficiency`/`sufficiency_reason`) already existed and was already
 * correct -- Phase 8.2 proved it against a real paper -- but nothing
 * in the UI ever rendered it. This does not compute a new verdict: it
 * calls the `/documents/{asset_id}/analyze` endpoint automatically,
 * using the run's own query as the goal, and renders exactly what comes
 * back. No gap text is invented here -- an item with no
 * `missing_information` renders no gap list at all, and the
 * `EmptyState`/upload action is the honest fallback while the check is
 * still running or found nothing to name.
 *
 * Scoped to the project's first asset: every real project in this
 * deployment has exactly one source paper, and `analyze` is a
 * per-asset endpoint with no run-level equivalent to call instead.
 *
 * Below the gap list, also offers the one deliberate way out of the
 * evidence boundary (Sprint 16 Phase 8.13): pressing the control here
 * is a second, explicit action after the refusal has already
 * rendered -- it never fires automatically, and the resulting
 * `unsourced` run is a separate row the backend creates via
 * `POST /research/runs/{run_id}/unsourced`, never a rewrite of this
 * one's `insufficient_evidence` verdict.
 */
export function EvidenceGapPanel({
  projectId,
  query,
  runId,
}: {
  projectId: string;
  query: string;
  runId: string;
}) {
  const unsourcedMutation = useMutation({
    mutationFn: () => createUnsourcedAnswer(runId),
  });

  const assetsQuery = useQuery({
    queryKey: ["assets", projectId],
    queryFn: () => assetsService.listAssets(projectId),
  });
  const asset = assetsQuery.data?.[0];

  const analysisQuery = useQuery({
    queryKey: ["document-analysis", asset?.id, query],
    queryFn: () =>
      analyzeResearchDocument(asset!.id, projectId, {
        goal: { type: "answer_question", description: query },
      }),
    enabled: Boolean(asset),
    // A best-effort check on top of an already-declined answer -- if
    // it fails, the reader still has the upload action below; retrying
    // a second model call for a nice-to-have isn't worth the wait.
    retry: false,
  });

  if (assetsQuery.isLoading || analysisQuery.isLoading) {
    return <Skeleton className="h-32" />;
  }

  const gaps = analysisQuery.data?.missing_information ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>What's missing</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {analysisQuery.data && (
          <p className="text-sm leading-relaxed text-foreground">
            {analysisQuery.data.sufficiency_reason}
          </p>
        )}

        {gaps.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {gaps.map((gap: ResearchGap, index: number) => (
              <li
                key={index}
                className="flex flex-col gap-1 rounded-lg bg-sunken p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge domain="gapClassification" value={gap.classification} />
                  <span className="text-sm font-medium text-foreground">{gap.gap_type}</span>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">{gap.description}</p>
                <p className="text-xs italic text-muted-foreground">Why it's needed: {gap.why_needed}</p>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={FileSearch}
            title="No specific gap was identified for this question."
            description="Uploading more source material to this project may still help future questions."
          />
        )}

        <div className="flex flex-col gap-3 pt-2">
          {!unsourcedMutation.data && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start"
              onClick={() => unsourcedMutation.mutate()}
              disabled={unsourcedMutation.isPending}
            >
              {unsourcedMutation.isPending
                ? "Asking without your papers…"
                : "Answer from general knowledge instead"}
            </Button>
          )}

          {unsourcedMutation.isError && (
            <p className="text-xs text-destructive">
              Could not generate an unsourced answer. Try again.
            </p>
          )}

          {unsourcedMutation.data && (
            <GeneralKnowledgeAnswer answer={unsourcedMutation.data.final_answer ?? ""} />
          )}
        </div>

        <UploadDropzone projectId={projectId} />
      </CardContent>
    </Card>
  );
}

/** A general-knowledge answer, in the one container that marks it as
 * such -- shared by this panel's opt-in answer and `ResearchResult`'s
 * automatic one, so the two can never drift apart in how clearly they
 * are labelled. Deliberately NOT a badge on a normal answer card -- a
 * badge does not survive copy-paste, and this container's whole purpose
 * is to stay unmistakable even after someone copies the text out of it
 * (Sprint 16 Phase 8.13 Part C). */
export function GeneralKnowledgeAnswer({ answer }: { answer: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    // The disclaimer is not added here -- it is already part of `answer`
    // (the backend writes it into the answer itself), so copying the
    // text verbatim is what keeps the warning attached once this leaves
    // the visually distinct container below and lands in, say, a draft
    // document.
    await navigator.clipboard.writeText(answer);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border-2 border-dashed border-warning/50 bg-warning/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-warning">
          <Lightbulb className="h-4 w-4" aria-hidden="true" />
          <span className="text-label uppercase">From general knowledge — not your documents</span>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={handleCopy}>
          {copied ? (
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <AnswerBody answer={answer} citations={[]} onSelect={() => {}} />
    </div>
  );
}
