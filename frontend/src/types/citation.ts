/** `ResearchRunDetail.citations` is typed as `Record<string, unknown>[]`
 * in the generated OpenAPI types (`src/types/api.d.ts`) — the backend
 * stores citations as JSONB with no strict Pydantic sub-schema, so
 * `openapi-typescript` cannot generate a stricter shape.
 *
 * This is the one hand-written UI type the design spec allows for
 * exactly that reason. Every field is optional and every read site
 * must use it that way: this describes what the backend has been
 * observed to send, not a contract it promises to send. Never assume
 * a field is present — render "Not provided" rather than a fabricated
 * value when it's missing (Phase 20/24: "only show fields actually
 * returned by the API", "do not invent fields").
 */
export interface Citation {
  id?: string;
  rank?: number;
  score?: number;
  title?: string;
  source?: string;
  snippet?: string;
  asset_id?: string;
  chunk_id?: string;
  provider?: string;
  file_name?: string;
  reference?: string;
  simulated?: boolean;
  /** The page a web result came from (web citations only). */
  url?: string;
  rerank_score?: number;
  retrieval_rank?: number;
  retrieval_score?: number;
  reranking_status?: string;
  relevance_threshold?: number;
}

/** `ResearchRunDetail.citations` arrives as `Record<string, unknown>[]`;
 * this narrows one entry to `Citation` without asserting fields that
 * were never validated. */
export function asCitation(raw: Record<string, unknown>): Citation {
  return raw as Citation;
}
