import { request } from "@/services/client";

export interface KnowledgeReadiness {
  project_id: string;
  configured_space: { id: string; provider: string; model: string; version: string; dimensions: number; document_task: string | null; query_task: string | null };
  active_space_id: string | null;
  total_chunks: number; searchable_chunks: number; legacy_chunks: number;
  ready: boolean; compatible: boolean;
  rebuild: { id: string; status: string; total: number; completed: number; failed: number; error: string | null } | null;
}

export function getKnowledgeReadiness(projectId: string) {
  return request<KnowledgeReadiness>(`/api/v1/knowledge-base/readiness?project_id=${encodeURIComponent(projectId)}`);
}

export function rebuildKnowledge(projectId: string) {
  return request<KnowledgeReadiness>(`/api/v1/knowledge-base/rebuild?project_id=${encodeURIComponent(projectId)}`, { method: "POST" });
}
