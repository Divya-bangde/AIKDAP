import { request } from "@/services/client";
import type { components } from "@/types/api";
import { pageQuery, type Page } from "@/services/pagination";
import { assertSession, sessionEpoch } from "@/lib/session-lifecycle";

type ResearchRunCreate = components["schemas"]["ResearchRunCreate"];
type ResearchRunAccepted = components["schemas"]["ResearchRunAccepted"];
type ResearchRunDetail = components["schemas"]["ResearchRunDetail"];
type ResearchRunRead = components["schemas"]["ResearchRunRead"];
type AnalyzeDocumentRequest = components["schemas"]["AnalyzeDocumentRequest"];
type ResearchDocumentUnderstanding = components["schemas"]["ResearchDocumentUnderstanding"];
type PaperImportRequest = { openalex_ids: string[] };
type PaperImportAccepted = components["schemas"]["PaperImportAccepted"];

export type ResearchRunSummary = Pick<ResearchRunRead, "id" | "project_id" | "query" | "status" | "grounding_status" | "created_at" | "completed_at" | "duration_ms"> & { citations?: ResearchRunRead["citations"] };

export function listResearchRunsPage(projectId?: string, cursor?: string | null, limit = 50, status?: string, groundingStatus?: string) {
  return request<Page<ResearchRunSummary>>(`/api/v1/research/runs/page?${pageQuery(cursor, limit, { project_id: projectId, status, grounding_status: groundingStatus })}`);
}

export type ComparisonSource = components["schemas"]["SourceReference"] & { chunk_id: string | null; quote: string | null };
export type SavedComparison = Omit<components["schemas"]["CrossPaperComparison"], "comparison_items"> & {
  artifact_id: string; created_at: string; goal: components["schemas"]["ResearchGoal"];
  status: "completed" | "insufficient_evidence"; insufficiency_reason: string | null; stale: boolean;
  source_versions: { asset_id: string; checksum: string; asset_version: number; chunk_fingerprint: string; extractor_version: string; model_identity: string; prompt_version: string }[];
  comparison_items: (Omit<components["schemas"]["ComparisonItem"], "source_references"> & { source_references: ComparisonSource[] })[];
};

export function restoreComparison(assetId: string, projectId: string) {
  return request<SavedComparison | null>(`/api/v1/research/documents/${assetId}/cross-paper-analysis?project_id=${encodeURIComponent(projectId)}`);
}

export function comparePapers(assetId: string, projectId: string, payload: components["schemas"]["CrossPaperAnalysisRequest"]) {
  return request<SavedComparison>(`/api/v1/research/documents/${assetId}/cross-paper-analysis?project_id=${encodeURIComponent(projectId)}`, { method: "POST", body: payload });
}

export function startResearchRun(payload: ResearchRunCreate) {
  return request<ResearchRunAccepted>("/api/v1/research/run", {
    method: "POST",
    body: payload,
  });
}

export function getResearchRun(runId: string) {
  return request<ResearchRunDetail>(`/api/v1/research/runs/${runId}`);
}

export async function listResearchRuns(projectId?: string) {
  // Full answers are needed for project exports; visible histories use compact
  // cursor summaries. Preserve this legacy full-record contract without a cap.
  const epoch = sessionEpoch();
  const runs: ResearchRunRead[] = [];
  for (let skip = 0; ; skip += 200) {
    assertSession(epoch);
    const query = new URLSearchParams({ limit: "200", skip: String(skip) });
    if (projectId) query.set("project_id", projectId);
    const page = await request<ResearchRunRead[]>(`/api/v1/research/runs?${query}`);
    assertSession(epoch);
    runs.push(...page);
    if (page.length < 200) return runs;
  }
}

export function createUnsourcedAnswer(runId: string) {
  return request<ResearchRunRead>(`/api/v1/research/runs/${runId}/unsourced`, {
    method: "POST",
  });
}

export function analyzeResearchDocument(
  assetId: string,
  projectId: string,
  payload: AnalyzeDocumentRequest
) {
  return request<ResearchDocumentUnderstanding>(
    `/api/v1/research/documents/${assetId}/analyze?project_id=${projectId}`,
    {
      method: "POST",
      body: payload,
    }
  );
}

export function importSuggestedPapers(runId: string, openalexIds: string[]) {
  return request<PaperImportAccepted>(`/api/v1/research/runs/${runId}/papers/import`, {
    method: "POST",
    body: { openalex_ids: openalexIds } satisfies PaperImportRequest,
  });
}
