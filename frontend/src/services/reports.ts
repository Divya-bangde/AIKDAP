import { saveBlob } from "@/lib/export";
import { request, requestBlob } from "@/services/client";
import type { components } from "@/types/api";

type ReportKind = components["schemas"]["ReportKind"];
type ReportGenerateRequest = components["schemas"]["ReportGenerateRequest"];
type ReportGenerationAccepted = components["schemas"]["ReportGenerationAccepted"];
type ReportRead = components["schemas"]["ReportRead"];
type BuildPlanRequest = components["schemas"]["BuildPlanRequest"];
type AssetRead = components["schemas"]["AssetRead"];

export function generateSynopsis(projectId: string, kind: ReportKind) {
  return request<ReportGenerationAccepted>(`/api/v1/projects/${projectId}/reports/synopsis`, {
    method: "POST",
    body: { kind } satisfies ReportGenerateRequest,
  });
}

export function generateBuildPlan(projectId: string, assetIds: string[]) {
  return request<ReportGenerationAccepted>(`/api/v1/projects/${projectId}/reports/build-plan`, {
    method: "POST",
    body: { asset_ids: assetIds } satisfies BuildPlanRequest,
  });
}

/** A project's synopses and build plans, newest first. Rows carry no
 * step trace; `getReport` fetches one report's trace. */
export function listReports(projectId: string) {
  return request<AssetRead[]>(`/api/v1/projects/${projectId}/reports`);
}

export function getReport(assetId: string) {
  return request<ReportRead>(`/api/v1/reports/${assetId}`);
}

export function retryReport(assetId: string) {
  return request<ReportGenerationAccepted>(`/api/v1/reports/${assetId}/retry`, { method: "POST" });
}

/** Downloads a completed report and saves it through the browser. */
export async function downloadReport(assetId: string, format: "docx" | "pdf", filename: string) {
  saveBlob(await requestBlob(`/api/v1/reports/${assetId}/download?format=${format}`), filename);
}
