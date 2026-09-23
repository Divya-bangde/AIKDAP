import { openEventStream, request } from "@/services/client";
import type { components } from "@/types/api";

type ResearchStepRead = components["schemas"]["ResearchStepRead"];
type StepStreamToken = components["schemas"]["StepStreamToken"];

/** Whose workflow steps: a research run, or a generated report (a
 * report run has no research-run row; its steps belong to the asset). */
export type StepOwner = { kind: "run" | "report"; id: string };

function stepsPath({ kind, id }: StepOwner): string {
  return kind === "run" ? `/api/v1/research/runs/${id}/steps` : `/api/v1/reports/${id}/steps`;
}

export function listSteps(owner: StepOwner) {
  return request<ResearchStepRead[]>(stepsPath(owner));
}

export function createStepStreamToken(owner: StepOwner) {
  return request<StepStreamToken>(`${stepsPath(owner)}/stream-token`, { method: "POST" });
}

export function openStepStream(owner: StepOwner, token: string) {
  return openEventStream(`${stepsPath(owner)}/stream?token=${encodeURIComponent(token)}`);
}
