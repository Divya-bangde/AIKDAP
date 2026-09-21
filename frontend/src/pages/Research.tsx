import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";

import { RowListSkeleton } from "@/components/common/Skeletons";
import { PageTransition } from "@/components/motion/PageTransition";
import { ResearchHistoryList } from "@/features/research/ResearchHistoryList";
import { ResearchPrompt } from "@/features/research/ResearchPrompt";
import { ResearchRunView } from "@/features/research/ResearchRunView";
import * as researchService from "@/services/research";

export function Research() {
  const { runId } = useParams<{ runId?: string }>();
  const [searchParams] = useSearchParams();
  const history = searchParams.get("history");

  if (runId) {
    return <ResearchRunView runId={runId} />;
  }

  if (history) {
    return <ResearchHistory groundedOnly={history === "grounded"} />;
  }

  return <ResearchPrompt />;
}

/** Every research run across all projects — the Command Center's
 * "Research Runs" and "Grounded Answers" tiles land here, the latter
 * pre-filtered to grounded runs. Shares the Dashboard's query key, so
 * arriving from the tile needs no second fetch. */
function ResearchHistory({ groundedOnly }: { groundedOnly: boolean }) {
  const runsQuery = useQuery({
    queryKey: ["research", "runs", "all"],
    queryFn: () => researchService.listResearchRuns(),
  });

  return (
    <PageTransition>
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {groundedOnly ? "Grounded Answers" : "Research History"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {groundedOnly
              ? "Research runs backed by cited evidence, across every project."
              : "Every research run across your projects."}
          </p>
        </div>
        {runsQuery.isLoading ? (
          <RowListSkeleton label="Loading research history" />
        ) : runsQuery.isError ? (
          <p className="text-sm text-muted-foreground">
            Research history is unavailable.
          </p>
        ) : (
          <ResearchHistoryList
            key={String(groundedOnly)}
            runs={runsQuery.data ?? []}
            initialStatus={groundedOnly ? "grounded" : null}
          />
        )}
      </div>
    </PageTransition>
  );
}
