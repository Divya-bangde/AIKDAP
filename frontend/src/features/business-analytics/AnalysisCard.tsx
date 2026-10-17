import { StatusBadge } from "@/components/common/StatusBadge";
import { WorkflowTimeline } from "@/features/workflow-timeline/WorkflowTimeline";
import { chartFigure, type AnalysisResultData, type ChartSpecData, type ChartFigure } from "@/features/business-analytics/analysis-chart";
import type { AnalysisRead } from "@/services/analytics";
import { ChartFrame } from "@/features/research/ChartFrame";
import { LazyRenderer } from "@/features/research/RendererBoundary";
import { downloadFile, fileSlug } from "@/lib/export";

// The Plotly bundle only loads when an analysis actually has a chart to show.
const loadPlotlyRenderer = () => import("@/features/research/PlotlyVisualization");

type Filter = { column: string; op: string; value: unknown };
type Metric = { column: string | null; agg: string; alias: string; as_share: boolean };
type AnalysisPlan = {
  unanswerable_reason: string | null;
  filters: Filter[];
  time_bucket: { column: string; grain: string } | null;
  group_by: string[];
  metrics: Metric[];
  sort: { by: string; descending: boolean } | null;
  limit: number | null;
  chart: ChartSpecData;
};

function asPlan(plan: AnalysisRead["plan"]): AnalysisPlan | null {
  return plan as AnalysisPlan | null;
}

function asResult(result: AnalysisRead["result"]): AnalysisResultData | null {
  return result as AnalysisResultData | null;
}

/** Splits narrative text on the analysis's `unverified_numbers` tokens,
 * wrapping each occurrence so an unverified figure reads as flagged
 * rather than trusted the same as everything else in the sentence. */
function NarrativeText({ narrative, unverified }: { narrative: string; unverified: string[] }) {
  if (unverified.length === 0) return <p className="text-sm">{narrative}</p>;
  // Longest-first: otherwise a shorter token like "12" matches inside "12%"
  // before the alternation ever gets to try the longer one.
  const sorted = [...unverified].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(${sorted.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  const parts = narrative.split(pattern);
  return (
    <p className="text-sm">
      {parts.map((part, index) =>
        unverified.includes(part) ? (
          <mark key={index} title="Not found in the result" className="underline decoration-dotted bg-transparent">
            {part}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </p>
  );
}

function HowComputed({ plan }: { plan: AnalysisPlan }) {
  return (
    <details className="mt-3">
      <summary className="cursor-pointer text-xs font-medium text-muted-foreground">How this was computed</summary>
      <div className="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
        {plan.filters.length > 0 && (
          <p>Filters: {plan.filters.map((f) => `${f.column} ${f.op} ${String(f.value)}`).join(", ")}</p>
        )}
        {plan.time_bucket && <p>Time bucket: {plan.time_bucket.column} ({plan.time_bucket.grain})</p>}
        {plan.group_by.length > 0 && <p>Group by: {plan.group_by.join(", ")}</p>}
        {plan.metrics.length > 0 && (
          <p>
            Metrics:{" "}
            {plan.metrics
              .map((m) => `${m.agg}(${m.column ?? "*"}) as ${m.alias}${m.as_share ? " (% of total)" : ""}`)
              .join(", ")}
          </p>
        )}
        {plan.sort && <p>Sort: {plan.sort.by} {plan.sort.descending ? "desc" : "asc"}</p>}
        {plan.limit !== null && plan.limit !== undefined && <p>Limit: {plan.limit}</p>}
      </div>
    </details>
  );
}

export function AnalysisCard({ analysis }: { analysis: AnalysisRead }) {
  const running = analysis.status !== "completed" && analysis.status !== "failed";
  const plan = asPlan(analysis.plan);
  const result = asResult(analysis.result);
  const chart = plan?.chart;
  const [resultExportError, setResultExportError] = useState<string | null>(null);
  let figure: ChartFigure | null = null;
  let chartError: string | null = null;
  if (analysis.status === "completed" && result && chart && chart.type !== "kpi") {
    try { figure = chartFigure(result, chart); }
    catch (error) { chartError = error instanceof Error ? error.message : "Chart could not be prepared. View the result table."; }
  }
  const exportReceivedResult = async () => {
    if (!result) return;
    try {
      await downloadFile(`${fileSlug(analysis.question)}-full-received-result.json`, JSON.stringify({ analysis_id: analysis.id, dataset_id: analysis.dataset_id, question: analysis.question, origin: "derived from dataset computation", computation: plan, result, display_reductions: figure?.reductions ?? [], scope: `Contains every received result row (${result.rows.length} of ${result.total_rows} computed rows); omitted source/result rows are not fabricated.` }, null, 2), "application/json");
      setResultExportError(null);
    } catch { setResultExportError("Result download failed. Retry the download or inspect the complete received result table below."); }
  };

  return (
    <div className="rounded-card bg-card p-6 shadow-subtle">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium">{analysis.question}</p>
        <StatusBadge domain="assetProcessing" value={analysis.status} />
      </div>

      {running && (
        <div className="mt-4 rounded-lg bg-sunken p-4">
          <WorkflowTimeline owner={{ kind: "report", id: analysis.id }} active />
        </div>
      )}

      {analysis.status === "failed" && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {analysis.error ?? "Analysis failed."}
        </p>
      )}

      {analysis.status === "completed" && (
        <div className="mt-3 flex flex-col gap-3">
          {result && chart?.type === "kpi" && (
            <div className="flex flex-wrap gap-4">
              {chart.y.map((y) => (
                <div key={y}>
                  <p className="text-label uppercase text-muted-foreground">{y}</p>
                  <p className="text-3xl font-semibold tracking-tight">{String(result.rows[0]?.[y] ?? "—")}</p>
                </div>
              ))}
            </div>
          )}

          {chartError && <p role="status" className="rounded-lg bg-sunken p-3 text-sm">Visualization unavailable: {chartError}</p>}
          {!result && chart && !["table", "kpi"].includes(chart.type) && <p role="status" className="text-sm">Visualization not produced: {plan?.unanswerable_reason ?? "Not enough result data for the requested chart."}</p>}
          {figure && <div className="flex min-w-0 flex-col gap-3">
            {figure.notices.map((notice) => <p key={notice} className="text-xs text-muted-foreground">{notice}</p>)}
            {figure.panels.map((panel) => <ChartFrame key={panel.id} title={panel.title} origin="derived from dataset computation" data={panel.data} layout={panel.layout} scope={figure.notices}
              context={[`Analysis: ${analysis.question}`, `Dataset: ${analysis.dataset_id}`, ...((plan?.filters ?? []).map((filter) => `Filter: ${filter.column} ${filter.op} ${String(filter.value)}`)), ...(plan?.time_bucket ? [`Time bucket: ${plan.time_bucket.column} (${plan.time_bucket.grain})`] : ["Time window: no time bucket declared."])]}
              metadata={{ analysis_id: analysis.id, dataset_id: analysis.dataset_id, computation: plan, source_scope: result?.scope ?? null, numeric_encoding: result?.numeric_encoding ?? null, received_trace_reductions: figure.reductions.filter((reduction) => panel.data.some((trace) => trace.uid === reduction.uid)) }}>
              {(visibleData) => <LazyRenderer label="chart" load={loadPlotlyRenderer} rendererProps={{ data: visibleData, layout: panel.layout }} />}
            </ChartFrame>)}
          </div>}

          {result && <div className="space-y-2"><button type="button" onClick={exportReceivedResult} className="rounded border border-border px-3 py-2 text-xs">Download full received result (JSON)</button><p className="text-xs text-muted-foreground">Full received result: {result.rows.length} of {result.total_rows} computed rows; the table and this download retain every received value before display reduction.</p></div>}
          {resultExportError && <p role="status" className="text-sm">{resultExportError}</p>}
          {result && (
            <details>
              <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                Showing {result.rows.length} of {result.total_rows}
              </summary>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr>
                      {result.columns.map((col) => (
                        <th key={col} className="border-b border-border px-2 py-1 font-medium">{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, index) => (
                      <tr key={index}>
                        {result.columns.map((col) => (
                          <td key={col} className="border-b border-border px-2 py-1">{String(row[col] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}

          {analysis.narrative ? (
            <NarrativeText narrative={analysis.narrative} unverified={analysis.unverified_numbers} />
          ) : result ? (
            <p className="text-sm text-muted-foreground">Explanation unavailable.</p>
          ) : null}

          {plan && <HowComputed plan={plan} />}
        </div>
      )}
    </div>
  );
}
import { useState } from "react";
