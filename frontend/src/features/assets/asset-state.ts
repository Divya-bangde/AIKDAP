import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];

const TERMINAL_PROCESSING_STATUSES = new Set([
  "completed",
  "failed",
  "unsupported",
  "ocr_required",
]);
const TERMINAL_EMBEDDING_STATUSES = new Set(["completed", "failed", "not_applicable"]);

/** Whether an asset has finished everything the backend will ever do to it.
 *
 * `processing_status` and `ai_profile` are genuinely decoupled state
 * machines, not one pipeline (see `AssetProcessingService.process_asset`'s
 * own docstring: "the deterministic pipeline already succeeded and
 * processing_status is final... only ai_profile / each chunk's own
 * embedding_status" changes after that). A naive
 * `processing_status`-only check stops polling the instant extraction
 * finishes, while Qwen/BGE-M3 are still running — caught live during
 * Sprint 9K's real end-to-end run, where the AI panel was still
 * "pending" seconds after polling had already stopped.
 *
 * `ai_profile.status`/`embedding_status` only ever leave "pending" if
 * `processing_status` reached `completed` (a failed/unsupported asset
 * never gets that far, and "pending" there is permanent and correct —
 * not something to wait on).
 *
 * A `source: "generated"` asset (a report/synopsis) never runs the
 * upload pipeline's AI-profile step at all -- `ReportService.
 * generate_synopsis` creates it with a default `AIProfile()`, whose
 * `status`/`embedding_status` stay "pending" forever. Waiting on those
 * fields for a generated asset would never settle, so it settles as
 * soon as `processing_status` itself reaches a terminal state. */
export function isSettled(asset: AssetRead): boolean {
  if (!TERMINAL_PROCESSING_STATUSES.has(asset.processing_status)) return false;
  if (asset.processing_status !== "completed") return true;
  if (asset.source === "generated") return true;
  return (
    asset.ai_profile.status !== "pending" &&
    TERMINAL_EMBEDDING_STATUSES.has(asset.ai_profile.embedding_status)
  );
}

export function allSettled(assets: AssetRead[]): boolean {
  return assets.every(isSettled);
}

/** Counts of real, backend-reported asset states — never estimated.
 * Used by the project workspace overview to describe the project's
 * knowledge state. */
export function knowledgeState(assets: AssetRead[]) {
  return {
    total: assets.length,
    extracted: assets.filter((a) => a.processing_status === "completed").length,
    understood: assets.filter((a) => a.ai_profile.status === "completed").length,
    embedded: assets.filter((a) => a.ai_profile.embedding_status === "completed").length,
  };
}
