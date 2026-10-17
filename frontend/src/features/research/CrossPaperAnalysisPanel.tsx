import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/common/ErrorState";
import { SourceViewer } from "@/features/research/SourceViewer";
import { EvidenceDrawer } from "@/features/research/EvidenceDrawer";
import { getAsset } from "@/services/assets";
import { comparePapers, restoreComparison, type ComparisonSource } from "@/services/research";
import type { Citation } from "@/types/citation";
import type { components } from "@/types/api";
import { useAuthStore } from "@/store/auth-store";

type Asset = components["schemas"]["AssetRead"];

export function CrossPaperAnalysisPanel({ primaryAssetId, projectId, papers }: {
  primaryAssetId: string; projectId: string; papers: Asset[];
}) {
  const client = useQueryClient();
  const sessionId = useAuthStore((state) => state.sessionId);
  const key = ["comparison", projectId, primaryAssetId, sessionId];
  const restored = useQuery({ queryKey: key, queryFn: () => restoreComparison(primaryAssetId, projectId) });
  const [supporting, setSupporting] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
  const [citation, setCitation] = useState<Citation | null>(null);
  const create = useMutation({
    mutationFn: () => comparePapers(primaryAssetId, projectId, { supporting_asset_ids: supporting, goal: { type: "compare", description: goal.trim() } }),
    onSuccess: (result) => client.setQueryData(key, result),
  });
  const openSource = useMutation({
    mutationFn: async (source: ComparisonSource) => {
      const asset = await getAsset(source.paper_id);
      return { asset_id: asset.id, chunk_id: source.chunk_id ?? undefined, title: asset.title, file_name: asset.file_name, snippet: source.quote ?? undefined } satisfies Citation;
    },
    onSuccess: setCitation,
  });
  const result = restored.data;
  useEffect(() => {
    if (result) { setSupporting(result.supporting_paper_ids); setGoal(result.goal.description); }
  }, [result?.artifact_id]);
  const isPdf = citation?.file_name?.toLowerCase().endsWith(".pdf") ?? false;

  return <section className="flex flex-col gap-4 rounded-card border p-5" aria-label="Cross-paper comparison">
    <h2 className="text-section">Cross-paper comparison</h2>
    {restored.isLoading && <p role="status">Restoring saved comparison…</p>}
    {restored.isError && <ErrorState error={restored.error} title="Couldn't restore comparison" />}
    <label className="flex flex-col gap-2 text-sm">Comparison goal
      <textarea className="rounded-md border bg-background p-2" value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="Which findings or methods should these papers be compared on?" />
    </label>
    <fieldset className="flex flex-col gap-2"><legend className="mb-2 text-sm font-medium">Supporting papers</legend>
      {papers.filter((paper) => paper.id !== primaryAssetId && paper.project_id === projectId && paper.asset_type === "document").map((paper) => <label key={paper.id} className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={supporting.includes(paper.id)} onChange={(event) => setSupporting((current) => event.target.checked ? [...current, paper.id] : current.filter((id) => id !== paper.id))} />{paper.title}
      </label>)}
      <p className="text-xs text-muted-foreground">{supporting.length} selected. Load more documents to select older papers.</p>
    </fieldset>
    <Button className="self-start" disabled={!goal.trim() || supporting.length === 0 || create.isPending || restored.isLoading} onClick={() => create.mutate()}>{create.isPending ? "Comparing passages…" : result ? "Recompute comparison" : "Compare papers"}</Button>
    {create.isError && <ErrorState error={create.error} />}
    {openSource.isError && <ErrorState error={openSource.error} title="Source unavailable" />}
    {result && <>
      <p className="text-xs text-muted-foreground">Saved {new Date(result.created_at).toLocaleString()} · {result.source_versions.length} source versions</p>
      {result.stale && <p role="alert" className="text-sm text-warning">Sources or analysis configuration changed. Recompute before using this comparison or opening its citations.</p>}
      {result.status === "insufficient_evidence" && <p role="status" className="text-sm">Insufficient evidence: {result.insufficiency_reason ?? "The available passages cannot support this comparison."}</p>}
      {result.comparison_items.map((item, index) => <article className="flex flex-col gap-2 rounded-md bg-sunken p-4" key={index}>
        <h3 className="font-semibold">{item.topic} · {item.relationship.replaceAll("_", " ")}</h3>
        <p className="text-sm"><strong>Primary:</strong> {item.primary_claim}</p>
        {item.supporting_claims.map((claim, i) => <p className="text-sm" key={i}><strong>Supporting:</strong> {claim}</p>)}
        <p className="text-sm">{item.evidence}</p>
        {item.source_references.map((source, i) => <div className="border-l-2 pl-3" key={`${source.paper_id}-${source.chunk_id}-${i}`}>
          {source.quote && <blockquote className="text-sm">{source.quote}</blockquote>}
          <Button size="sm" variant="ghost" disabled={!source.chunk_id || result.stale || openSource.isPending} onClick={() => openSource.mutate(source)}>Open source: {source.paper_title}</Button>
        </div>)}
      </article>)}
      {result.gap_resolutions.map((gap, index) => <p className="text-sm" key={index}><strong>{gap.gap_description}:</strong> {gap.resolution_state} — {gap.explanation}</p>)}
      {result.hypotheses.map((hypothesis) => <p className="text-sm" key={hypothesis.id}><strong>Hypothesis:</strong> {hypothesis.description} — {hypothesis.rationale}</p>)}
    </>}
    <SourceViewer citation={isPdf ? citation : null} onClose={() => setCitation(null)} />
    <EvidenceDrawer citation={!isPdf ? citation : null} index={null} onClose={() => setCitation(null)} />
  </section>;
}
