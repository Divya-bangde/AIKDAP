import { request, requestForm } from "@/services/client";
import type { components } from "@/types/api";

export type ExperimentPlan = components["schemas"]["ExperimentPlan"];
type ExperimentPlanCreateFromEquation =
  components["schemas"]["ExperimentPlanCreateFromEquation"];
type ExperimentPlanCreateFromUnderstanding =
  components["schemas"]["ExperimentPlanCreateFromUnderstanding"];
type ExperimentPlanUpdateRequest = components["schemas"]["ExperimentPlanUpdateRequest"];
type ExperimentVariantCreateRequest = components["schemas"]["ExperimentVariantCreateRequest"];
type ExperimentSweepRequest = components["schemas"]["ExperimentSweepRequest"];
export type TestCaseImportResponse = components["schemas"]["TestCaseImportResponse"];
export type VisualizationData = components["schemas"]["VisualizationData"];

export function createExperimentFromEquation(payload: ExperimentPlanCreateFromEquation) {
  return request<ExperimentPlan>("/api/v1/research/experiments/from-equation", {
    method: "POST",
    body: payload,
  });
}

export function createExperimentFromUnderstanding(
  payload: ExperimentPlanCreateFromUnderstanding
) {
  return request<ExperimentPlan>("/api/v1/research/experiments/from-understanding", {
    method: "POST",
    body: payload,
  });
}

export function getExperimentPlan(planId: string) {
  return request<ExperimentPlan>(`/api/v1/research/experiments/${planId}`);
}

export function listExperimentPlans(projectId: string) {
  return request<ExperimentPlan[]>(
    `/api/v1/research/experiments?project_id=${projectId}`
  );
}

export function updateExperimentPlan(planId: string, payload: ExperimentPlanUpdateRequest) {
  return request<ExperimentPlan>(`/api/v1/research/experiments/${planId}`, {
    method: "PATCH",
    body: payload,
  });
}

export function createExperimentVariant(
  planId: string,
  payload: ExperimentVariantCreateRequest
) {
  return request<ExperimentPlan>(`/api/v1/research/experiments/${planId}/variants`, {
    method: "POST",
    body: payload,
  });
}

export function createExperimentSweep(planId: string, payload: ExperimentSweepRequest) {
  return request<ExperimentPlan>(`/api/v1/research/experiments/${planId}/sweep`, {
    method: "POST",
    body: payload,
  });
}

export async function importExperimentTestCases(planId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return requestForm<TestCaseImportResponse>(
    `/api/v1/research/experiments/${planId}/test-cases/import`,
    formData
  );
}

export function getExperimentVisualization(
  planId: string,
  inputName: string,
  outputName: string,
  groupBy?: string
) {
  const params = new URLSearchParams({ input_name: inputName, output_name: outputName });
  if (groupBy) params.set("group_by", groupBy);
  return request<VisualizationData>(
    `/api/v1/research/experiments/${planId}/visualization?${params.toString()}`
  );
}
