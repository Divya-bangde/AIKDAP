import { request } from "@/services/client";
import type { components } from "@/types/api";

export type AnalysisRead = components["schemas"]["AnalysisRead"];
export type DatasetProfileRead = components["schemas"]["DatasetProfileRead"];
export type KaggleFileRead = components["schemas"]["KaggleFileRead"];

export const getProfile = (datasetId: string) =>
  request<DatasetProfileRead>(`/api/v1/analytics/datasets/${datasetId}/profile`);

export const listAnalyses = (datasetId: string) =>
  request<AnalysisRead[]>(`/api/v1/analytics/datasets/${datasetId}/analyses`);

export const createAnalysis = (datasetId: string, question: string) =>
  request<AnalysisRead>(`/api/v1/analytics/datasets/${datasetId}/analyses`, {
    method: "POST",
    body: { question },
  });

export const listKaggleFiles = (owner: string, dataset: string) =>
  request<KaggleFileRead[]>(
    `/api/v1/analytics/kaggle/${encodeURIComponent(owner)}/${encodeURIComponent(dataset)}/files`,
  );

export const importKaggle = (projectId: string, owner: string, dataset: string, fileName: string) =>
  request<{ status: string }>(`/api/v1/projects/${projectId}/analytics/kaggle/import`, {
    method: "POST",
    body: { owner, dataset, file_name: fileName },
  });
