import { ChartFrame, type ChartSource } from "@/features/research/ChartFrame";
import { LazyRenderer } from "./RendererBoundary";
import type { components } from "@/types/api";

/** The chart/diagram spec the backend validated before storing it
 * (`schemas.Visualization`). Produced only when the question asked for a
 * visual, so most answers carry none. */
export interface VisualizationSpec {
  kind: "chart2d" | "chart3d" | "diagram";
  title?: string;
  data?: Record<string, unknown>[];
  layout?: Record<string, unknown>;
  mermaid?: string | null;
  origin?: "measured" | "derived" | "illustrative" | "user_entered" | "unknown";
  intent?: components["schemas"]["VisualizationIntent"];
  scope?: { label?: string | null; partial: boolean; description?: string | null } | null;
  provenance?: { trace: number; field: string; point: number; inputs: { citation_id: string; reference: string; unit: string; value: number }[]; formula: string; unit: string }[];
}

type CanonicalOutcome = components["schemas"]["VisualizationOutcome"];
export type VisualizationOutcome = Omit<CanonicalOutcome, "repair_attempts"> & Partial<Pick<CanonicalOutcome, "repair_attempts">>;

/** Visual completion is independent of answer success. Legacy charts have no
 * value verification metadata and cannot acquire trust merely by being saved. */
export type VisualEvidence = ChartSource;
export function ResearchVisualization({ spec, outcome, evidence = [], metadata }: { spec?: VisualizationSpec | null; outcome?: VisualizationOutcome | null; evidence?: VisualEvidence[]; metadata?: Record<string, unknown> }) {
  if (!outcome && !spec) return null;
  const visible = !!spec && outcome?.status === "produced" && (spec.kind === "diagram" || outcome.verification !== "unverified");
  return <section aria-label="Visualization outcome" className="min-w-0 space-y-2">
    <p role="status" className="text-sm font-medium">Visualization {visible ? "produced" : outcome?.status === "produced" ? "withheld" : outcome?.status.replaceAll("_", " ") ?? "unverified"}</p>
    <p className="text-sm text-muted-foreground">{outcome?.reason ?? "This saved visualization has no independent value-verification metadata and is withheld. The answer and evidence remain available."}</p>
    {outcome?.requested?.type && <p className="text-xs text-muted-foreground">Requested visual: {outcome.requested.type}{outcome.requested.axis_semantics ? `; axis: ${outcome.requested.axis_semantics}` : ""}.</p>}
    {outcome?.requested?.interpretation && <p className="text-xs text-muted-foreground">Interpretation: {outcome.requested.interpretation}</p>}
    {outcome?.next_action && <p className="text-sm">{outcome.next_action}</p>}
    {visible && <AnswerVisualization spec={spec!} evidence={evidence} metadata={metadata} />}
  </section>;
}

// Both renderers are multi-megabyte and most answers have no visual, so
// each is split into its own chunk and loaded only when one is needed.
const loadPlotlyRenderer = () => import("@/features/research/PlotlyVisualization");
const loadDiagramRenderer = () => import("@/features/research/MermaidDiagram");

export function AnswerVisualization({ spec, evidence = [], metadata }: { spec: VisualizationSpec; evidence?: VisualEvidence[]; metadata?: Record<string, unknown> }) {
  const referenced = new Set(spec.provenance?.flatMap((point) => point.inputs.map((input) => input.citation_id)) ?? []);
  const sources = evidence.filter((item) => referenced.has(item.id));
  return (
    <div className="min-w-0 space-y-2">
      <ChartFrame title={spec.title || "Research visualization"} origin={spec.origin ?? "unknown (legacy metadata unavailable)"} data={spec.data} layout={spec.layout} sources={sources}
        context={[...new Set((spec.provenance ?? []).map((point) => `Trace ${point.trace + 1} ${point.field} unit: ${point.unit || "unspecified"}.`))]}
        scope={spec.scope ? [`${spec.scope.partial ? "Partial data. " : ""}${spec.scope.label ?? ""} ${spec.scope.description ?? ""}`] : ["Scope and time window were not supplied."]}
        metadata={{ ...metadata, provenance: spec.provenance ?? [], intent: spec.intent ?? null }} relationshipSource={spec.kind === "diagram" ? spec.mermaid ?? "" : undefined}>
      {(visibleData) => <>
        {spec.kind === "diagram" ? (
          <LazyRenderer label="diagram" load={loadDiagramRenderer} rendererProps={{ source: spec.mermaid ?? "" }} />
        ) : (
          <LazyRenderer label="chart" load={loadPlotlyRenderer} rendererProps={{ data: visibleData, layout: spec.layout ?? {} }} />
        )}
      </>}
      </ChartFrame>
      {!!spec.provenance?.length && <details className="text-xs"><summary className="cursor-pointer">Value provenance and transformations</summary><ul className="mt-2 space-y-1">{spec.provenance.map((point, index) => <li key={index}>Trace {point.trace + 1}, {point.field}[{point.point}]: {point.formula} ({point.inputs.map((input) => `${input.value} ${input.unit}; ${input.reference}`).join("; ")}) → {point.unit || "unit unspecified"}</li>)}</ul></details>}
    </div>
  );
}
