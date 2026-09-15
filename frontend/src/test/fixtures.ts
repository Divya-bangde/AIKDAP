import type { components } from "@/types/api";

type AIProfile = components["schemas"]["AIProfile"];
type AssetRead = components["schemas"]["AssetRead"];
type ProjectRead = components["schemas"]["ProjectRead"];
type ReportRead = components["schemas"]["ReportRead"];
type ResearchRunRead = components["schemas"]["ResearchRunRead"];
type ResearchStepRead = components["schemas"]["ResearchStepRead"];
type UserRead = components["schemas"]["UserRead"];

/** A complete `AIProfile` carrying the backend's own defaults, so a
 * fixture spells out only the fields its test is about -- and a new
 * backend field lands here once, not in every test that builds an asset. */
export function aiProfile(overrides: Partial<AIProfile> = {}): AIProfile {
  return {
    status: "pending",
    embedding_status: "pending",
    truncated: false,
    processed_sections: 0,
    total_sections: 0,
    ...overrides,
  };
}

export function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: "p1",
    owner_id: "u1",
    name: "Poultry Intelligence",
    description: null,
    project_type: "research",
    status: "active",
    color: null,
    icon: null,
    persona_override: null,
    effective_persona: "researcher",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeUser(overrides: Partial<UserRead> = {}): UserRead {
  return {
    id: "u1",
    email: "researcher@example.com",
    full_name: null,
    is_active: true,
    persona: "researcher",
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeRun(overrides: Partial<ResearchRunRead> = {}): ResearchRunRead {
  return {
    id: "r1",
    project_id: "p1",
    owner_id: "u1",
    task_id: null,
    query: "What challenges does ABC Poultry face?",
    status: "completed",
    include_assets: true,
    include_web: false,
    max_results: 5,
    objective: null,
    plan: null,
    final_answer: "Two challenges.",
    citations: [],
    grounding_status: "grounded",
    error_message: null,
    celery_task_id: null,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    duration_ms: 4200,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

/** A fully settled document unless overridden. */
export function makeAsset(overrides: Partial<AssetRead> = {}): AssetRead {
  return {
    id: "a1",
    project_id: "p1",
    owner_id: "u1",
    title: "report.pdf",
    description: null,
    asset_type: "document",
    status: "active",
    mime_type: "application/pdf",
    file_name: "report.pdf",
    file_extension: "pdf",
    file_size: 1024,
    checksum: "abc",
    source: "upload",
    version: 1,
    tags: [],
    metadata: {},
    ai_profile: aiProfile({ status: "completed", embedding_status: "completed" }),
    created_by: null,
    processing_status: "completed",
    processing_error: null,
    processing_started_at: null,
    processing_completed_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

/** A GENERATED report asset (study summary) with its step trace. */
export function makeReport(overrides: Partial<ReportRead> = {}): ReportRead {
  return {
    ...makeAsset({
      title: "Study Summary",
      asset_type: "summary",
      source: "generated",
      mime_type: "application/json",
      file_name: "study-summary.json",
      file_extension: "json",
      file_size: 0,
    }),
    steps: [],
    ...overrides,
  };
}

/** One report-run step, completed unless overridden. */
export function makeStep(overrides: Partial<ResearchStepRead> = {}): ResearchStepRead {
  return {
    id: "s1",
    run_id: null,
    asset_id: "a1",
    attempt: 1,
    step_index: 0,
    node_name: "collect_documents",
    title: "Collect project documents",
    status: "completed",
    summary: "Collected 1 processed document(s).",
    output_payload: { document_count: 1 },
    error_message: null,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    duration_ms: 12,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}
