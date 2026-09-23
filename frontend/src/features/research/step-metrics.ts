import type { components } from "@/types/api";

type ResearchStepRead = components["schemas"]["ResearchStepRead"];

export interface StepMetric {
  label: string;
  value: string;
  /** Machine values (model ids, statuses) render monospaced. */
  mono?: boolean;
}

function num(payload: Record<string, unknown>, key: string): number | undefined {
  const raw = payload[key];
  return typeof raw === "number" ? raw : undefined;
}

function str(payload: Record<string, unknown>, key: string): string | undefined {
  const raw = payload[key];
  return typeof raw === "string" && raw.length > 0 ? raw : undefined;
}

function list(payload: Record<string, unknown>, key: string): unknown[] | undefined {
  const raw = payload[key];
  return Array.isArray(raw) ? raw : undefined;
}

/** Extracts the metrics worth showing for one pipeline stage.
 *
 * Every entry is read from a key the backend actually wrote into
 * `output_payload`, and any key that is absent produces no entry at
 * all — a missing measurement is never rendered as zero, "n/a", or a
 * placeholder. The per-node key sets below were taken from the real
 * stored payloads of a completed run, not from assumption.
 *
 * `output_payload` is untyped JSONB in the backend (no per-node
 * Pydantic schema), which is why every read is type-guarded rather
 * than cast. */
export function stepMetrics(step: ResearchStepRead): StepMetric[] {
  const metrics = payloadMetrics(step);
  // Workflow timeline: what the step's LLM calls spent, from the
  // step's own columns. Absent (not zero) when nothing was reported.
  const tokens = [
    step.input_tokens != null ? `${step.input_tokens} in` : null,
    step.output_tokens != null ? `${step.output_tokens} out` : null,
  ].filter(Boolean);
  if (tokens.length > 0) metrics.push({ label: "Tokens", value: tokens.join(" · ") });
  const models = list(step.metadata ?? {}, "models");
  if (models && models.length > 1) metrics.push({ label: "Models", value: models.join(", "), mono: true });
  return metrics;
}

function payloadMetrics(step: ResearchStepRead): StepMetric[] {
  const payload = step.output_payload;
  if (!payload) return [];

  const metrics: StepMetric[] = [];
  const push = (label: string, value: string | number | undefined, mono = false) => {
    if (value === undefined) return;
    metrics.push({ label, value: String(value), mono });
  };

  switch (step.node_name) {
    case "planner": {
      push("Intent", str(payload, "intent"), true);
      push("Strategy", str(payload, "strategy"), true);
      push("Steps", list(payload, "steps")?.length);
      break;
    }
    case "router": {
      const selected = list(payload, "selected_agents");
      const skipped = list(payload, "skipped_agents");
      if (selected) push("Agents", selected.join(", "), true);
      if (skipped && skipped.length > 0) push("Skipped", skipped.join(", "), true);
      break;
    }
    case "asset_retrieval": {
      push("Chunks retrieved", num(payload, "document_count"));
      push("Reranking", str(payload, "reranking_status"), true);
      break;
    }
    case "web_research": {
      push("References", num(payload, "document_count"));
      break;
    }
    case "context_builder": {
      push("Received", num(payload, "received"));
      push("Included", num(payload, "included"));
      push("Citations", num(payload, "citation_count"));
      push("Context chars", num(payload, "context_characters"));
      break;
    }
    case "synthesis": {
      push("Provider", str(payload, "provider"), true);
      push("Model", str(payload, "model"), true);
      push("Evidence supplied", num(payload, "evidence_supplied"));
      push("Grounded citations", num(payload, "grounded_citations"));
      const simulated = num(payload, "simulated_citations");
      if (simulated !== undefined && simulated > 0) push("Simulated withheld", simulated);
      const rejected = list(payload, "rejected_citation_ids");
      if (rejected && rejected.length > 0) push("Rejected citations", rejected.length);
      const latency = num(payload, "latency_ms");
      if (latency !== undefined) push("Latency", `${latency} ms`);
      const attempts = num(payload, "llm_attempts");
      if (attempts !== undefined) push("Attempts", attempts);
      if (payload.fallback_used === true) {
        push("Fallback", "used", true);
        push("Primary", str(payload, "primary_model"), true);
        push("Primary error", str(payload, "primary_error_type"), true);
      }
      break;
    }
    default:
      break;
  }

  return metrics;
}

/** The model a step used, short form ("groq/llama-3.1" -> "llama-3.1"). */
export function modelLabel(step: ResearchStepRead): string | undefined {
  return step.model_name ? step.model_name.split("/").pop() : undefined;
}

/** The knowledge-base-vs-web decision, in plain language, when the
 * step recorded one, e.g. "Web search: not used. Found enough in your
 * documents." Undefined for every step that made no such decision. */
export function decisionLine(step: ResearchStepRead): string | undefined {
  const metadata = step.metadata ?? {};
  if (typeof metadata.used_web !== "boolean") return undefined;
  const reason = str(metadata, "reason");
  return `Web search: ${metadata.used_web ? "used" : "not used"}.${reason ? ` ${reason}` : ""}`;
}

/** The evidence funnel, assembled only from fields the API actually
 * returns for this run.
 *
 * This is deliberately NOT the backend's internal `candidate_count` /
 * `rejected_count` pair: those are written to the worker's logs but
 * are not exposed on any step payload, so the UI cannot show them
 * without inventing them. What it shows instead is the funnel that IS
 * exposed, stage by stage, each from its own real field. Any stage
 * whose source field is missing is omitted rather than guessed. */
export function evidenceFunnel(steps: ResearchStepRead[]) {
  const find = (node: string) => steps.find((s) => s.node_name === node)?.output_payload ?? null;

  const retrieval = find("asset_retrieval");
  const context = find("context_builder");
  const synthesis = find("synthesis");

  const stages: { label: string; value: number; detail: string }[] = [];

  const retrieved = retrieval ? num(retrieval, "document_count") : undefined;
  if (retrieved !== undefined) {
    stages.push({
      label: "Retrieved",
      value: retrieved,
      detail: "Knowledge-base chunks",
    });
  }

  const included = context ? num(context, "included") : undefined;
  const received = context ? num(context, "received") : undefined;
  if (included !== undefined) {
    stages.push({
      label: "In context",
      value: included,
      detail: received !== undefined ? `of ${received} received` : "Merged for synthesis",
    });
  }

  const supplied = synthesis ? num(synthesis, "evidence_supplied") : undefined;
  if (supplied !== undefined) {
    stages.push({
      label: "Evidence supplied",
      value: supplied,
      detail: "Passed the relevance gate",
    });
  }

  const grounded = synthesis ? num(synthesis, "grounded_citations") : undefined;
  if (grounded !== undefined) {
    stages.push({
      label: "Cited",
      value: grounded,
      detail: "Validated in the answer",
    });
  }

  const withheld = synthesis ? num(synthesis, "simulated_citations") : undefined;

  return { stages, withheld };
}
