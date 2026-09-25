# Milestone 6 — Business Analytics: Design

Status: Approved 2026-09-25 (revised in planning: step endpoints and Kaggle import reuse existing paths)

## Goal

A user picks a CSV/XLSX dataset in a project, asks a business question in plain
English, and receives numbers, a chart, and a short grounded narrative. Follow-up
questions build on the dataset's previous analyses. Datasets come from uploads or
from Kaggle import.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Scope | Question answering over a dataset (with auto-profile) | Core "completes work" loop; dashboards later |
| Engine | LLM emits a validated `AnalysisPlan`; pandas executes it in-process | No generated code runs; deterministic, traceable, LLM-free tests |
| Persistence | Each analysis is a GENERATED `Asset` (`asset_metadata["analysis"]`, tags `analysis`, `dataset:<id>`); trace in `research_steps` keyed by `asset_id` | Mirrors reports / experiment plans; no migration |
| Follow-ups | Per-dataset thread; planner sees last 3 analyses | Refinements without restating |
| Kaggle | Included; server-side credentials; httpx client, no `kaggle` package | No new dependency |

## Architecture

LangGraph graph in `app/agents/analytics/`, linear, mirroring `app/agents/reports/`
(`NodeSpec`, `instrument`, `LLMGateway`):

| Node | Critical | Behaviour |
|---|---|---|
| `load_dataset` | yes | Read file via `StorageProvider` into pandas; build/cache profile in the dataset asset's `asset_metadata["profile"]` |
| `plan_analysis` | yes | LLM → `AnalysisPlan` from profile + question + last 3 thread analyses; validate; one repair retry; may be unanswerable |
| `execute` | yes | Pure `execute_plan(df, plan) -> AnalysisResult` |
| `explain` | no | LLM narrative from result table only; failure leaves result + chart |
| `validate` | yes | Every number in the narrative must match a result value within rounding; unmatched are flagged |

Frozen-workflow mapping: Intent Analysis + Planner = `plan_analysis`; Specialized Agent
= `execute`; Response Aggregator = `explain`; Validation = `validate`; Explainable AI =
step trace + stored plan.

Feature module `app/modules/business_analytics/`: `router.py`, `service.py`,
`schemas.py`, `repository.py` (no `models.py` — no new table). Celery tasks
`workers.run_analysis`, `workers.import_kaggle_dataset`. Kaggle client in
`app/integrations/kaggle/`.

## AnalysisPlan

```python
Filter:     column, op in {eq,ne,gt,gte,lt,lte,in,not_in,between,contains,is_null,not_null}, value
TimeBucket: column, grain in {day,week,month,quarter,year}
Metric:     column | None (count only), agg in {sum,mean,median,min,max,count,nunique}, alias, as_share=False
Sort:       by, descending=True
ChartSpec:  type in {bar,line,pie,table,kpi}, x | None, y: list[str], series | None
AnalysisPlan:
  unanswerable_reason: str | None
  filters (<=10), time_bucket | None, group_by (<=2), metrics (1..5),
  sort | None, limit (1..1000, default 50), chart
```

Validation (no LLM): referenced columns exist; sum/mean/median only on numeric columns;
time bucket only on datetime (or date-parseable per profile); chart fields are plan
output columns. Errors carry a `difflib` "did you mean" suggestion. Unanswerable plans
may omit metrics/chart.

Executor order: filter → time bucket (`dt.to_period`) → `groupby().agg()` → shares →
sort → limit. No `eval`/`query`/string expressions. Output: columns, JSON-safe rows,
`total_rows` before limit, chart spec.

## Limits (settings, `.env`)

- `ANALYTICS_MAX_FILE_MB=50`
- `ANALYTICS_MAX_ROWS=500000` (load stops at cap; result reports truncation)
- XLSX: first sheet unless `sheet` given.
- In-memory load in the worker (`ponytail:` ceiling; DuckDB/chunked reads if needed).

Profile: row count, truncated flag; per column: name, dtype, null %, distinct count,
min/max (numeric/date), up to 5 sample values. The LLM sees the profile, never raw rows.

## API (`/api/v1`)

| Method | Path | Result |
|---|---|---|
| GET | `/analytics/datasets/{dataset_id}/profile` | Profile |
| POST | `/analytics/datasets/{dataset_id}/analyses` `{question, sheet?}` | 202, analysis asset queued |
| GET | `/analytics/datasets/{dataset_id}/analyses` | Thread, oldest first |
| GET | `/analytics/analyses/{asset_id}` | Full analysis |
| GET | `/analytics/kaggle/{owner}/{dataset}/files` | CSV/XLSX files + sizes |
| POST | `/projects/{project_id}/analytics/kaggle/import` `{owner, dataset, file_name}` | 202, import queued |

CSV/XLSX uploads without an explicit type become `DATASET`.

Step trace and live stream: the existing `/reports/{asset_id}/steps`,
`/steps/stream-token` and `/steps/stream` endpoints already serve any owned GENERATED
asset, so analyses reuse them (no duplicate endpoints).

Kaggle import reuses `AssetService.create_imported_asset(asset_type=DATASET)` and runs
the processing pipeline inline, exactly like the OpenAlex paper import. The asset
appears once the download succeeds (no placeholder row).

## Errors

- 404 unknown / not-owned asset; 409 not a DATASET or still processing; 422 empty or
  >1000-char question; 503 Kaggle not configured.
- Critical-node failure → asset FAILED, error on step, later nodes skipped.
- Unanswerable → completed with reason, no chart.
- `explain` failure → completed, "narrative unavailable".
- Stale runs failed by the existing reconciliation sweep.
- Kaggle slugs validated `^[A-Za-z0-9._-]+$`; download aborted past size limit; zip
  unpacked with stdlib; imported file enters the normal processing pipeline.

## Frontend

"Analytics" tab in `ProjectDetail.tsx`; code in `frontend/src/features/business-analytics/`:
`DatasetPicker`, `KaggleImportDialog`, `DatasetProfileCard`, `AnalysisThread`,
`AnalysisCard` (live step timeline, theme-aware Plotly chart, result table,
narrative with unverified-number marking, "How this was computed").

## Testing

Backend pytest: plan validation, executor (table-driven), profile, grounding, graph
with fake LLM gateway, router, Kaggle client via httpx `MockTransport`. Frontend
Vitest: `AnalysisCard` states, `KaggleImportDialog`. Manual E2E in browser preview.

## Out of scope

Cross-dataset joins, formulas/derived columns, generated-code execution, scheduled
dashboards, Kaggle competitions.
