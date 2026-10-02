import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileText } from "lucide-react";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { RowListSkeleton } from "@/components/common/Skeletons";
import { Card, CardContent } from "@/components/ui/card";
import { ReportRunPanel, isReportTerminal } from "@/features/reports/ReportRunPanel";
import { usePolling } from "@/hooks/usePolling";
import { formatRelativeTime } from "@/lib/format";
import * as reportsService from "@/services/reports";

const KIND_LABELS: Record<string, string> = {
  study_summary: "Study summary",
  project_synopsis: "Project synopsis",
  build_plan: "Build plan",
};

export function reportsListKey(projectId: string) {
  return ["reports", "list", projectId];
}

/** The project's Reports tab: every synopsis and build plan it has
 * produced, each rendered with the same `ReportRunPanel` the generate
 * dialogs use, so progress, retry and download behave identically in
 * both places. Polls while any report is still generating. */
export function ReportsList({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();

  const reportsQuery = usePolling({
    queryKey: reportsListKey(projectId),
    queryFn: () => reportsService.listReports(projectId),
    isTerminal: (reports) => reports.every(isReportTerminal),
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) => reportsService.retryReport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: reportsListKey(projectId) }),
  });

  if (reportsQuery.isLoading) return <RowListSkeleton label="Loading reports" />;
  if (reportsQuery.isError) return <ErrorState error={reportsQuery.error} title="Couldn't load reports" />;

  const reports = reportsQuery.data ?? [];
  if (reports.length === 0) {
    return (
      <EmptyState
        icon={FileText}
        title="No reports yet."
        description="Generate a synopsis or a build plan from the project header; it will appear here."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {reports.map((report) => {
        const kind = String(report.metadata?.kind ?? "");
        const retryingThis = retryMutation.variables === report.id;
        return (
          <Card key={report.id}>
            <CardContent className="flex flex-col gap-3 p-4">
              <div className="flex items-center gap-3">
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <p className="line-clamp-1 text-sm font-medium">{report.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {KIND_LABELS[kind] ?? "Report"} · {formatRelativeTime(report.created_at)}
                  </p>
                </div>
              </div>
              <ReportRunPanel
                report={report}
                started
                timedOut={false}
                onRetry={(id) => retryMutation.mutate(id)}
                retrying={retryingThis && retryMutation.isPending}
                retryError={retryingThis ? retryMutation.error : null}
              />
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
