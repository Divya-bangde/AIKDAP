# Persona-Based Features — Design

Date: 2026-09-13
Status: Approved in discussion, pending spec review
Source: Team feedback on the research workspace

## Summary

Four additions, driven by team feedback:

1. **Persona** — users identify as Student, Researcher, or Project Builder; projects may override.
2. **Paper suggestions** — when a research answer has evidence gaps, suggest relevant papers from OpenAlex, with one-click "Add & re-run".
3. **Synopsis** (Student) — generate a Study Summary or a formal Project Synopsis from the project's documents; download as DOCX or PDF.
4. **Build plan** (Builder) — from selected papers, research further and recommend tools plus a build process; download as DOCX or PDF.

The frozen enterprise workflow is unchanged. Every addition is an agent or tool inside the existing Planner → Orchestrator → Agents → Aggregator → Validation → XAI flow.

Architecture note: `CLAUDE.md` marks v1.0 frozen and forbids inventing features. These features are a team-requested scope addition; the milestone list in `CLAUDE.md` should be updated to record them before implementation begins.

## 1. Persona

### Data

- `User.persona`: enum `student | researcher | builder`, non-null, default `researcher` (existing users keep today's experience).
- `Project.persona_override`: same enum, nullable. Null means inherit the user's persona.
- Effective persona = `project.persona_override or user.persona`, computed in one backend helper and returned on project responses. The frontend never derives it.
- One Alembic migration adds both columns.

### Where it is set

- Signup: required choice "I am a… Student / Researcher / Project builder".
- Profile settings: editable at any time.
- Project Settings: "Use my default (<persona>)" or an explicit persona.

### Effect

Persona controls **visibility only, never permissions**. All endpoints accept any persona.

| Effective persona | Visible by default |
|---|---|
| Researcher | Current research workspace + paper suggestions |
| Student | The above + **Generate synopsis** button |
| Builder | The above + **Get build plan** button |

Paper suggestions are shown for every persona, because they only appear when a gap exists.

## 2. Paper suggestions

### Behaviour

- The research answer is produced exactly as today (project knowledge base, then Tavily).
- Papers are **never** fed into synthesis; the answer stays grounded only in what the user has.
- When synthesis reports `missing_information` (`ResearchGap` list, `modules/research/schemas.py`), OpenAlex is searched using the question plus the gap descriptions.
- A panel shows: "These papers could strengthen this answer — add them to your project." Each card: title, authors, year, citation count, link.
- No gaps → no panel.

### Architecture

- New non-critical agent `paper_suggestion`, registered in `AGENT_REGISTRY` (`agents/planner/registry.py`), so it is instrumented and appears as `skipped` in traces when not run.
- New edge in `agents/planner/graph.py`: after the final synthesis (after the bounded web-fallback loop), route to `paper_suggestion` when gaps exist, else `END`.
- `OpenAlexProvider` follows the `TavilyWebResearchProvider` pattern: direct `httpx` calls, no SDK. Settings: `openalex_api_key: SecretStr | None`, `openalex_timeout: float`. Blank key is treated as unset (same validator style as Tavily).
- Abstracts are reconstructed from OpenAlex's `abstract_inverted_index`.
- Results persist on the research run as new JSONB column `suggested_papers` (migration). Each entry: `openalex_id`, `title`, `authors`, `year`, `cited_by_count`, `landing_url`, `oa_pdf_url | null`, `relevance_note`.

## 3. Add & re-run

### Behaviour

- The user ticks one or more suggested papers and clicks **Add & re-run**.
- Papers with an open-access PDF are imported. Paywalled papers show "Open on publisher site" only.
- When all selected imports reach a final state, **one** new research run starts with the original question, linked to the original run.
- A notification (existing notifications module) announces the re-run result.
- Imported papers become normal project assets and are reused by future questions.

### Architecture

- `POST /api/v1/research/runs/{run_id}/papers/import` with `{openalex_ids: [...]}`. IDs must come from that run's `suggested_papers` (no arbitrary URLs).
- Celery chain per paper: download OA PDF → existing upload validators (size, MIME/magic bytes) → create asset (`source=IMPORTED`) → existing processing pipeline (extract, chunk, embed).
- A chord callback starts the re-run once every paper has completed or failed.
- New nullable column `parent_run_id` on research runs (migration) so the Workflow Timeline shows "Re-run with N added papers".

## 4. Reports base (`modules/reports`)

The existing, empty `modules/reports` folder hosts both synopsis and build plan. No new root folders, no renames.

- Files: `router.py`, `service.py`, `schemas.py`, `repository.py`, `export.py`.
- Outputs are generated assets (`AssetSource.GENERATED`); structured sections are stored as JSON on the asset.
- `GET /api/v1/reports/{asset_id}/download?format=docx|pdf` renders on demand from the stored sections, so both formats always match and nothing is stored twice.
  - DOCX: `python-docx` (already installed).
  - PDF: `reportlab` (already installed as a test dependency; promote to runtime).
- Generation runs as a Celery job; the asset carries a status (`pending | running | completed | failed`) and error.
- Orchestration: small linear LangGraph graphs in `agents/reports/`, reusing `agents/planner/tracking.instrument` for the same trace, timeline, and failure policy. Agents communicate through shared state only.

## 5. Synopsis (Student)

- `POST /api/v1/projects/{project_id}/reports/synopsis` with `{kind: "study_summary" | "project_synopsis"}`. Uses all processed documents in the project.
- Asset type: `SUMMARY` for study summary, `REPORT` for project synopsis.
- **Study summary**: key themes, main findings per document, how the documents relate.
- **Project synopsis** sections: Title, Abstract, Introduction, Problem Statement, Objectives, Literature Review, Methodology, Expected Outcomes, References.
- Graph: collect per-document `ai_profile` summaries/topics → per-section knowledge-base retrieval → per-section LLM write with document citations → coverage check.
- References are built only from the uploaded documents.
- A section with no supporting content is marked "Not covered by your documents" rather than fabricated.

## 6. Build plan (Builder)

- `POST /api/v1/projects/{project_id}/reports/build-plan` with `{asset_ids: [...]}`. The UI shows a paper picker with all processed documents ticked by default.
- Asset type: `REPORT`.
- Graph:
  1. **Extract** — method/algorithm, models, datasets, evaluation metrics, compute needs, stated limitations.
  2. **Research further** — OpenAlex for follow-up and improved methods; Tavily for open-source implementations, libraries, and dataset sources (with live links).
  3. **Recommend tools** — grouped by stage (data, modelling, backend, evaluation, deployment); each with a reason tied to the paper, plus alternatives.
  4. **Recommend process** — phases from reproducing the baseline to a working product; each with steps, a definition of done, and risks.
- Out of scope: converting the process into `tasks` module items.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| OpenAlex down / timeout | `paper_suggestion` step fails (non-critical); answer unaffected; trace records it |
| No OpenAlex key | Step skipped with explicit reason; no simulated results |
| Paper has no OA PDF | Card offers publisher link, no import |
| Download fails / not a PDF / too large | Existing validators reject; card shows error; re-run proceeds with the rest |
| All selected imports fail | No re-run; notification explains why |
| Synopsis section uncovered | Section marked "Not covered by your documents" |
| LLM failure mid-report | Asset marked `failed` with error; no partial document presented as complete |
| Report on a project with no processed documents | `422` with a clear message |
| Build-plan `asset_ids` not owned or not in project | `404`, consistent with existing ownership checks |

## 8. Testing

Backend (`pytest`):
- `OpenAlexProvider` with mocked `httpx`: parsing, inverted-index abstract reconstruction, timeout, missing key.
- Graph: `paper_suggestion` runs only with gaps, is skipped otherwise, and its failure does not fail the run.
- Import chain: partial failure still re-runs; all-fail does not; `parent_run_id` is linked; only IDs from the run's suggestions are accepted.
- Persona: user default, project override, effective-persona helper.
- Reports: section generation with a fake LLM, uncovered sections, DOCX/PDF export (re-open the file and assert headings).

Frontend (Vitest):
- Persona-driven button visibility.
- Paper panel renders only when gaps exist; Add & re-run selection behaviour.

## 9. Build order

Each step is one feature per commit/PR and independently shippable.

1. Persona — migration, signup/profile/project settings, effective-persona helper, button visibility.
2. Paper suggestions — OpenAlex provider, graph node, `suggested_papers`, panel.
3. Add & re-run — import endpoint, Celery chain, `parent_run_id`, notification.
4. Reports base + synopsis (both kinds) with DOCX/PDF export.
5. Build plan — reuses the reports base plus OpenAlex and Tavily.

## Out of scope

- arXiv and Semantic Scholar providers (OpenAlex only for now).
- Project-level "Suggested reading" panel (the search can be reused later).
- Paper picker for synopsis (uses all documents).
- Converting build-plan processes into tasks.
