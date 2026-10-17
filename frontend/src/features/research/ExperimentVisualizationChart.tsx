import { useState } from "react";
import { LazyRenderer } from "./RendererBoundary";

import { ChartFrame } from "@/features/research/ChartFrame";
import { EvidenceDrawer } from "@/features/research/EvidenceDrawer";
import { isPdfSource, SourceViewer } from "@/features/research/SourceViewer";
import { getAsset } from "@/services/assets";
import type { VisualizationData } from "@/services/experiments";
import type { Citation } from "@/types/citation";

const loadPlotlyRenderer = () => import("@/features/research/PlotlyVisualization");

/**
 * Renders raw trace data the backend computed from the plan's actual
 * test cases -- never a server-rendered image, never data this
 * component invents itself (Sprint 16 Phase 6, Part H/R). `note`, when
 * present, describes a trend only; it is never rendered as a causal
 * claim, because the backend never generates one.
 */
export function ExperimentVisualizationChart({ data }: { data: VisualizationData }) {
  const [citation, setCitation] = useState<Citation | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const series = data.series ?? [];
  const openSource = async (source: NonNullable<NonNullable<VisualizationData["series"][number]["source_references"]>[number]>) => {
    const reference = [source.page == null ? null : `Page ${source.page}`, source.chunk_index == null ? null : `chunk ${source.chunk_index}`, source.evidence_scope].filter(Boolean).join("; ");
    setSourceError(null);
    try {
      const asset = await getAsset(source.paper_id);
      setCitation({ title: asset.title, asset_id: asset.id, file_name: asset.file_name, chunk_id: source.chunk_id ?? undefined, snippet: source.quote ?? undefined, reference });
    } catch {
      setCitation({title:source.paper_title,chunk_id:source.chunk_id ?? undefined,snippet:source.quote ?? undefined,reference});
      setSourceError("The original document could not be opened. The saved source reference remains inspectable; retry after checking source access.");
    }
  };
  const hasAnyPoints = series.some((s) => (s.x?.length ?? 0) > 0);

  if (!hasAnyPoints) {
    return (
      <p className="text-sm text-muted-foreground">
        No test cases have both the selected input and output yet -- import test cases to see
        this chart.
      </p>
    );
  }

  const traces = series.map((s) => ({
          x: s.x,
          y: s.y,
          type: s.kind === "bar" ? "bar" : "scatter",
          mode: s.kind === "line" ? "lines+markers" : "markers",
          name: s.name,
        }));
  const layout = {
          autosize: true,
          height: 360,
          margin: { t: 20, r: 20, b: 50, l: 60 },
          xaxis: { title: { text: data.x_label } },
          yaxis: { title: { text: data.y_label } },
        };
  const sources = series.flatMap((s, seriesIndex) => (s.source_references ?? []).flatMap((source, point) => source ? [{id:`case-${seriesIndex}-${point}`,title:source.paper_title,asset_id:source.paper_id,chunk_id:source.chunk_id ?? undefined,reference:source.quote ?? undefined,onOpen:()=>void openSource(source)}] : []));
  return <>
  <ChartFrame title={`${data.y_label || "Output"} by ${data.x_label || "input"}`} origin={data.origin === "expected_test_case_values" ? "expected test-case values (not observed execution results)" : "unknown (legacy metadata unavailable)"} finding={data.note ?? undefined} context={[
    "Units are supplied only when present in axis labels.",
    ...(data.plan_id ? [`Plan: ${data.plan_id}${data.plan_version == null ? " (version unavailable)" : `; version ${data.plan_version}`}.`] : []),
    ...(data.input_name ? [`Selected input: ${data.input_name}; output: ${data.output_name ?? "not supplied"}${data.group_by ? `; group: ${data.group_by}` : ""}.`] : []),
  ]} scope={[data.scope_description ?? "Test-case scope was not supplied.", ...(data.partial ? ["Partial data: test cases without the selected input are excluded."] : [])]} sources={sources}
  metadata={{plan_id:data.plan_id ?? null,plan_version:data.plan_version ?? null,input_name:data.input_name ?? null,output_name:data.output_name ?? null,group_by:data.group_by ?? null,total_test_cases:data.total_test_cases ?? null,plotted_test_cases:data.plotted_test_cases ?? null,excluded_missing_input:data.excluded_missing_input ?? null,received_test_case_provenance:series.map((s)=>({name:s.name,test_case_ids:s.test_case_ids ?? null,source_references:s.source_references ?? null}))}}
  data={traces} layout={layout}>
    {(visible) => <LazyRenderer label="chart" load={loadPlotlyRenderer} rendererProps={{data:visible,layout}} />}
  </ChartFrame>
  {sourceError && <p role="status" className="text-sm">{sourceError}</p>}
  {isPdfSource(citation ?? {}) ? <SourceViewer citation={citation} onClose={()=>setCitation(null)}/> : <EvidenceDrawer citation={citation} index={null} onClose={()=>setCitation(null)}/>}
  </>;
}
