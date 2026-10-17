import { request } from "@/services/client";
import type { components } from "@/types/api";
import { collectPages, pageQuery, type Page } from "@/services/pagination";

type ProjectCreate = components["schemas"]["ProjectCreate"];
type ProjectRead = components["schemas"]["ProjectRead"];
type ProjectUpdate = components["schemas"]["ProjectUpdate"];

export interface WorkspaceSummary {
  projects: number; assets: number; documents: number; runs: number; grounded_answers: number;
}

export function getWorkspaceSummary(projectId?: string) {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return request<WorkspaceSummary>(`/api/v1/projects/summary${query}`);
}

export function listProjectsPage(cursor?: string | null, limit = 50) {
  return request<Page<ProjectRead>>(`/api/v1/projects/page?${pageQuery(cursor, limit)}`);
}

export function listProjects() {
  return collectPages((cursor) => listProjectsPage(cursor, 200));
}

export function createProject(payload: ProjectCreate) {
  return request<ProjectRead>("/api/v1/projects", { method: "POST", body: payload });
}

export function getProject(projectId: string) {
  return request<ProjectRead>(`/api/v1/projects/${projectId}`);
}

export function deleteProject(projectId: string) {
  return request<void>(`/api/v1/projects/${projectId}`, { method: "DELETE" });
}

export function updateProject(projectId: string, payload: ProjectUpdate) {
  return request<ProjectRead>(`/api/v1/projects/${projectId}`, { method: "PATCH", body: payload });
}
