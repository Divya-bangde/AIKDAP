import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/common/ErrorState";
import { getKnowledgeReadiness, rebuildKnowledge } from "@/services/knowledge";
import { useAuthStore } from "@/store/auth-store";

const WORKING = new Set(["queued", "pending", "running", "validating"]);

export function useKnowledgeReadiness(projectId: string) {
  const sessionId = useAuthStore((state) => state.sessionId);
  return useQuery({
    queryKey: ["knowledge-readiness", projectId, sessionId],
    queryFn: () => getKnowledgeReadiness(projectId),
    enabled: Boolean(projectId),
    refetchInterval: (query) => query.state.data?.rebuild && WORKING.has(query.state.data.rebuild.status) ? 3000 : false,
  });
}

export function KnowledgeReadiness({ projectId }: { projectId: string }) {
  const query = useKnowledgeReadiness(projectId);
  const client = useQueryClient();
  const sessionId = useAuthStore((state) => state.sessionId);
  const rebuild = useMutation({
    mutationFn: () => rebuildKnowledge(projectId),
    onSuccess: (data) => client.setQueryData(["knowledge-readiness", projectId, sessionId], data),
  });
  if (query.isLoading) return <p role="status" className="text-sm">Checking searchable coverage…</p>;
  if (query.isError) return <ErrorState error={query.error} title="Search readiness unavailable" />;
  const data = query.data;
  if (!data) return null;
  const working = Boolean(data.rebuild && WORKING.has(data.rebuild.status));
  return <section className="flex flex-col gap-3 rounded-card border p-5" aria-label="Search readiness">
    <h3 className="font-semibold">{data.ready ? "Knowledge base ready" : "Knowledge base needs attention"}</h3>
    <p className="text-sm">{data.searchable_chunks} of {data.total_chunks} passages searchable in the configured embedding space.</p>
    <p className="text-xs text-muted-foreground">{data.configured_space.provider} · {data.configured_space.model} · {data.configured_space.dimensions} dimensions</p>
    {!data.compatible && <p role="alert" className="text-sm">The active embeddings are incompatible with the configured model. Search is unavailable until a rebuild passes validation.</p>}
    {data.legacy_chunks > 0 && <p className="text-sm">{data.legacy_chunks} legacy passages have no verified embedding space.</p>}
    {data.rebuild && <p role="status" className="text-sm">Rebuild {data.rebuild.status}: {data.rebuild.completed} of {data.rebuild.total} complete; {data.rebuild.failed} failed.</p>}
    {data.rebuild?.error && <p role="alert" className="text-sm text-destructive">{data.rebuild.error}</p>}
    {rebuild.isError && <ErrorState error={rebuild.error} />}
    {!data.ready && data.total_chunks > 0 && <Button variant="outline" className="self-start" disabled={working || rebuild.isPending} onClick={() => rebuild.mutate()}>
      {working || rebuild.isPending ? "Rebuilding…" : data.rebuild ? "Resume rebuild" : "Rebuild embeddings"}
    </Button>}
  </section>;
}
