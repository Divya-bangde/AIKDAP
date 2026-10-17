import { request, requestBlob, requestForm } from "@/services/client";
import type { components } from "@/types/api";
import { collectPages, pageQuery, type Page } from "@/services/pagination";

type AssetRead = components["schemas"]["AssetRead"];
type ChunkLocationRead = components["schemas"]["ChunkLocationRead"];

export function listAssetsPage(projectId?: string, cursor?: string | null, limit = 50) {
  return request<Page<AssetRead>>(`/api/v1/assets/page?${pageQuery(cursor, limit, { project_id: projectId })}`);
}

export function getAsset(assetId: string) {
  return request<AssetRead>(`/api/v1/assets/${assetId}`);
}

export function listAssets(projectId?: string) {
  return collectPages((cursor) => listAssetsPage(projectId, cursor, 200));
}

/** Matches `Body_upload_asset_api_v1_assets_upload_post` exactly:
 * `project_id` and `file` are the only required multipart fields. */
export function uploadAsset(projectId: string, file: File) {
  const form = new FormData();
  form.append("project_id", projectId);
  form.append("file", file);
  return requestForm<AssetRead>("/api/v1/assets/upload", form);
}

/** Deletes the asset and its stored file. The backend cascades the
 * delete to `knowledge_chunks` at the database level (`ondelete=
 * CASCADE` on `asset_id`) — no orphaned chunks are left behind. */
export function deleteAsset(assetId: string) {
  return request<void>(`/api/v1/assets/${assetId}`, { method: "DELETE" });
}

/** The asset's raw file as a Blob, fetched through the authenticated
 * client (a plain `<a href>` or pdf.js URL fetch could not send the
 * bearer token). */
export function downloadAssetFile(assetId: string) {
  return requestBlob(`/api/v1/assets/download/${assetId}`);
}

/** Where a cited chunk sits in its source PDF (click-to-source). */
export function getChunkLocation(chunkId: string) {
  return request<ChunkLocationRead>(`/api/v1/knowledge-base/chunks/${chunkId}/location`);
}
