# Nicolas — Batch 1 backend checkpoint

Scope: V01, V03, V04; analytics contract dependencies for V02, V05, V06. Existing dirty user files were preserved with targeted additions. No UI implementation owned here.

## Verified baseline findings

Current `schemas.Visualization` accepted trace dictionaries with only a list somewhere, arbitrary layout, numeric-year point lists silently converted to lines; `GroundedSynthesizer` authorized chart output from prose citation status alone. Invalid/missing output was dropped without independent completion status. `ResearchRunRead` silently hid invalid saved charts. `_resolve_full_evidence_text` fetched chunk IDs without checking asset/project identity. Analytics `total_rows` was computed after the dataset cap and did not communicate that cap to the chart.

## Implemented contract

- Canonical `backend/app/modules/research/visualization_contract.py`: discriminated bar/scatter/scatter3d, pie, heatmap/contour/surface traces; exact shape/finite numeric/paired array/rectangular grid/2D–3D validation; numeric/date/log semantics; no hidden nonpositive log observations, precision-losing integers or ranges clipping plotted data. Layout and nested title objects use an allowlist. Null observations remain gaps; `connectgaps` can only be false.
- `backend/app/agents/planner/visualization.py`: every numeric coordinate/cell needs exactly one provenance entry, exact citation reference, matching asset/chunk, actual source quote and indexed numeric input token. Supplied-context membership alone does not establish the value. Asset chunks must also resolve in the current project. Category/row/column coordinates must match an unambiguous exact local evidence observation; substring matches and swapped categories/cells are rejected.
- Identity, sum, difference, ratio and a closed conversion registry (g/kg, m/km, fraction/%) are evaluated deterministically. Inputs retain references/units/formula, output unit, explicit optional rounding. Evidence numeric tokens are compared as exact Decimal values to input round-trip decimal representations; nonzero underflow and unsafe integers fail. Formulas use exact rational arithmetic and decimal conversion factors. Explicit rounding is rational decimal ties-to-even. Identity/zero/integer/subnormal/explicitly rounded values require exact equality. Only nonintegral, unrounded derived output permits one binary64 ULP around the correctly rounded exact result; no absolute or relative tolerance floor. Physical source units must immediately qualify the indexed number; axis titles must retain the verified output unit.
- Origins measured/derived/illustrative/user_entered/unknown remain separate. Actual source context containing illustrative/hypothetical/synthetic/simulated/sample-values qualifications conservatively forces illustrative, regardless of model origin. Unknown/user-entered values cannot enter from the synthesis path. A trusted user-input pathway does not yet exist.
- `visualization_outcome` is independent of answer completion: status produced/not_enough_data/unsupported/failed, reason, next_action, requested intent, verification and repair_attempts (0 or 1). Unsupported named radar/etc. requests cannot silently produce bars. Missing visual data and empty-evidence/extractive/unsourced paths report status. Generic chart interpretations are visible. One deterministic point-list normalization reuses existing output with explicit type; no extra model/stage runs occur.
- Canonical API `ResearchRunRead.visualization` is typed `Visualization|null`, with before-parse legacy normalization. Legacy saved charts without independent verification are withheld with failed/unverified explanation while preserving answers/citations. No fabricated backfill.
- `028_visualization_outcome` adds nullable research_runs JSONB. Synthesis result → research state → node → completion service persists intent/status. Verified visual references are unioned into citation evidence access.
- Analytics ChartSpec adds optional metric_units and ordering {mode: trend|ordinal|trajectory, x_type?: numeric|date, domain?: []}. DatasetProfile currently has no trusted units: validator clears generated metric_units, so model assertions cannot combine incompatible axes. Newton owns ordering/panels/trace mapping.
- Analytics result scope carries partial, dataset_truncated, label, description at execution. Research pies conservatively say Share of displayed categories and unknown full distribution coverage; no unproved Other value.

## Configurable bounds

Environment values are read on process startup by the canonical contract. Defaults: VISUALIZATION_MAX_TRACES=32; VISUALIZATION_MAX_POINTS=10000 per trace; VISUALIZATION_MAX_TOTAL_POINTS=50000 points/cells; VISUALIZATION_MAX_GRID_CELLS=40000; VISUALIZATION_MAX_LABEL_LENGTH=500; VISUALIZATION_MAX_BYTES=2000000 UTF-8 serialized JSON bytes; VISUALIZATION_MAX_DIAGRAM_CHARS=20000. Pie category cap is min(50, max points). Bytes are checked using incremental JSON encoding before trace/provenance model allocation; raw LLM JSON parsing necessarily precedes that check. Browser exact integer bound is 2^53−1.

## Verification

Offline deterministic regression command (repository root):

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_visualization_contract.py backend/tests/test_grounded_synthesis.py backend/tests/test_research_web_and_visuals.py backend/tests/test_analytics_executor.py backend/tests/test_analytics_plan.py backend/tests/test_analytics_graph.py backend/tests/test_analytics_grounding.py backend/tests/test_analytics_dataset.py backend/tests/test_claim_wiring.py -q --tb=short
```

Checkpoint before final typed API read change: **176 passed, 1 warning, 4.16s, exit 0**. Warning is existing LangChain pending deprecation of allowed_objects. Final rerun recorded below when completed.

Disposable migration command (backend directory):

```powershell
.venv/Scripts/python.exe scripts/verify_visualization_migration.py
```

**Exit 0**. New Docker pgvector:pg17 fixture only; created container removed in finally. Verified 027→028 preserves existing answer/chart and NULL legacy outcome; JSONB outcome round trips; 028→027 removes only the new column; re-upgrade preserves answer/chart and honestly NULL outcome. No user database or saved projects accessed.

OpenAPI export imports `app.main.app` and calls app.openapi directly without lifespan/startup/provider calls. Snapshot: `docs/visualization-evidence-2026-10-15/openapi-batch1.json`. Client command:

```powershell
frontend/node_modules/.bin/openapi-typescript.cmd docs/visualization-evidence-2026-10-15/openapi-batch1.json -o frontend/src/types/api.d.ts
```

Generation exit 0 (openapi-typescript7.13.0). Existing current schema/routes are regenerated together; shared file ownership was agreed with Newton/root.

## Review resolutions and limits

Reginald's material findings were resolved with targeted adversarial tests: incorrect category number within real quote; category substring identity; distant illustrative qualification promoted measured; missing no-evidence visual status; numeric/log axis mismatch; unsupported request status; ambiguous interpretation; swapped grid cells; malformed scalar/null trace entries throwing before validation; model-invented units.

Verification intentionally fails closed on ambiguous prose/table formatting and unsupported transformations. This may withhold a legitimate complex source chart until structured extraction is available. The verifier does not claim a general semantic understanding of arbitrary scientific tables. There is no independent analytics-result-to-research conversion path yet; direct analytics remains deterministic pandas execution. Source context qualification is conservative keyword detection; absent historical metadata stays unknown. Source evidence can only be as complete as the authorized retrieved chunk. Live providers are unverified. Root/Newton own real-browser, screenshots, full frontend integration, performance and batch gate acceptance.


## V06 research followup and final checkpoint

Research lines now sort after independent verification using explicit numeric/date/ordinal semantics, optional intent.domain, or declared trend inference with exposed interpretation. Unknown line intent retains original path order and explains that choice; trajectory intent/query always retains sequence. Numeric/date duplicates (including equal instants at different UTC offsets), null x, invalid dates and mixed timezone semantics fail clearly. Each trace sorts x/y/z together; original provenance.point indices are remapped and then independently reverified in an end-to-end regression. Null y remains an unbridged gap. An absent interior ordinal-domain point conservatively withholds the visual and requests explicit null observations; no synthetic observation/provenance is invented. Calendar month order is explicit; future/past months outside the observed interval are not invented.

Final expanded regression: **190 passed, 1 existing warning, exit 0** (elapsed recorded from tool output at handoff). Same exact nine-file command above. Final typed OpenAPI includes canonical Visualization/trace/layout/provenance/intent models plus the new optional ordinal domain; snapshot/client regeneration exit 0. API ownership released after final generation.

Final review scope includes V06 numeric/date/month/custom-domain sorting, gaps and trajectory behavior. Real-browser acceptance remains owned by root/Newton; these backend tests are not live-model or graphical-fidelity evidence.
