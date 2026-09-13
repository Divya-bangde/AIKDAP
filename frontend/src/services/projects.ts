import { request } from "@/services/client";
import type { components } from "@/types/api";

type ProjectCreate = components["schemas"]["ProjectCreate"];
type ProjectRead = components["schemas"]["ProjectRead"];
type ProjectUpdate = components["schemas"]["ProjectUpdate"];

export function listProjects() {
  return request<ProjectRead[]>("/api/v1/projects");
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
