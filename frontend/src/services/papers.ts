import { request } from "@/services/client";
import type { components } from "@/types/api";

type PaperGraph = components["schemas"]["PaperGraph"];
type ProjectPaperImportAccepted = components["schemas"]["ProjectPaperImportAccepted"];

export function getPaperGraph(projectId: string, includeExternal: boolean) {
  return request<PaperGraph>(
    `/api/v1/projects/${projectId}/paper-graph?include_external=${includeExternal}`,
  );
}

/** Queue an outside work's open-access PDF for import into the project. */
export function importOutsidePaper(projectId: string, openalexId: string) {
  return request<ProjectPaperImportAccepted>(`/api/v1/projects/${projectId}/papers/import`, {
    method: "POST",
    body: { openalex_id: openalexId },
  });
}
