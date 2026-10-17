# Nicolas — experiment renderer contract and analytics precision followup

Approved scope: Batch 1 V01/V03/V06 followup needed by the shared Batch 2 chart frame/export path. No UI edits. Existing dirty executor/router/schema/service changes were preserved with targeted additions.

## Verified current-code gaps

The experiment visualization endpoint had its own permissive VisualizationData/Series contract outside research Visualization budgets. Production choose_series_kind emitted only bar/line/scatter, but schemas allowed arbitrary strings. Input/output strings were converted to float without rejecting NaN/Infinity. Repeated imports appended an unbounded number of cases. Numeric duplicates such as 1 and 1.0 produced a misleading connecting line. The chart uses expected_outputs, never observed execution outputs, but previously exposed no result-role/provenance metadata. Deterministic analytics serialization used pandas.to_json defaults (ten decimal places), and Python integers above exact browser precision became silently rounded JSON numbers.

## Changes

- `backend/app/modules/research/experiment_visualization.py`: read-only preflight before point arrays/group sorting/trace models. Uses canonical research trace/point/total/label/byte budgets. Conservative UTF-8 envelope/title/note reserve plus actual point/case/source-reference bytes. Rejects oversize with actionable validation; no truncation or mutation. Source quote context capped5000 characters and source labels at canonical label budget.
- `experiment_schemas.py`: closed Literal bar/line/scatter kinds; kind consistency, paired arrays, finite values, labels, total points, actual serialized UTF-8 bytes, source/case alignment and scope-count reconciliation. Actual byte validation dumps UUID/source identities in JSON mode. Empty charts remain valid. Unknown legacy metadata remains unknown.
- `experiment_service.py`: ownership remains enforced before preflight. Missing input cases counted/excluded explicitly; missing/nonnumeric expected output retained as null. All plotted arrays and provenance use the same stable numeric sort. No source/provenance invented. Invalid/oversized chart preserves the plan and test cases; users can inspect existing plan/table/source data.
- `router.py`: only the experiment visualization endpoint maps its validation errors to actionable422; other experiment endpoint status behavior remains unchanged.
- `planner/experiment.py`: equivalent numeric x strings normalize only for duplicate detection; raw x/table text remains preserved. Duplicate numeric coordinates select scatter. Existing categorical bar and unique numeric line policy stays deterministic.
- Original experiment numeric strings exceeding exact browser integer precision (2^53−1), or underflowing to zero, are refused before float conversion. This conservative policy may withhold scientifically meaningful large integer-looking quantities until explicit supported scaling is available; exact original test-case/source text remains available.
- `analytics/executor.py`: native scalar serialization retains Python float precision. Unsafe browser integers become base10 strings. Result adds `numeric_encoding: Record<string,'exact_integer_string'>` and `chart_withheld_reason` only when needed. Table values remain exact; client must withhold numeric plotting rather than coerce these strings. Nonfinite computed output raises an explicit computation error. No arithmetic/aggregation redesign.

## Expected-value metadata

VisualizationData fields: origin expected_test_case_values or unknown; plan_id/version; input_name/output_name/group_by; total_test_cases/plotted_test_cases/excluded_missing_input; partial; scope_description. Per series: test_case_ids and source_references aligned with x/y; missing source is null. Labels explicitly state expected values are not observed execution results. No display truncation occurs: an over-budget visual is refused.

No migration: metadata is computed from existing owned plan/version/test cases. Legacy fields are nullable/unknown; no fabricated backfill.

## Exact clean verification

Repository-root offline expanded command:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_visualization_contract.py backend/tests/test_grounded_synthesis.py backend/tests/test_research_web_and_visuals.py backend/tests/test_analytics_executor.py backend/tests/test_analytics_plan.py backend/tests/test_analytics_graph.py backend/tests/test_analytics_grounding.py backend/tests/test_analytics_dataset.py backend/tests/test_claim_wiring.py backend/tests/test_experiment_visualization_bounds.py -q --tb=short
```

**225 passed, 1 existing warning, 5.10s, exit0.** Includes route422, preallocation rejection, source/ID sorting alignment, NaN/Infinity/unsafe integer/underflow, equivalent numericduplicates, exact budget boundaries, unsafe integer exact table encoding and small-float round-trip.

Disposable integration (backend directory):

```powershell
.venv/Scripts/python.exe scripts/check.py integration tests/test_experiment_visualization.py tests/test_experiment_visualization_bounds.py tests/test_analytics_executor.py tests/test_analytics_graph.py
```

**95 passed, 1 existing warning, 3.87s, exit0.** Docker pgvector PostgreSQL and Redis created with random local ports, migrated through028, then both containers removed. Existing actual persistence/import/visualization integration tests ran without skips. No saved user project/database touched. An earlier developer run exposed UUID serialization in byte accounting; JSON-mode serialization fixed it before this clean rerun.

Warning in both commands: existing LangChainPendingDeprecationWarning for allowed_objects.

## Canonical API generation

App OpenAPI generated directly without app lifespan/startup/provider calls into `docs/visualization-evidence-2026-10-15/openapi-batch2.json`. Generation:

```powershell
frontend/node_modules/.bin/openapi-typescript.cmd docs/visualization-evidence-2026-10-15/openapi-batch2.json -o frontend/src/types/api.d.ts
```

**Exit0**, openapi-typescript7.13.0. Includes closed kinds, expected/unknown origin, plan/selection/scope, aligned case/source fields. Root owns frontend consumption and graphical/export verification. `numeric_encoding` lives inside existing generic analytics result JSON and needs no additional generated structural API field.

Reginald reviewed ownership/bounds, expected-only provenance, numericduplicate policy and native/exact serialization; no remaining material backend blocker. Final clean commands/results supplied for the review record.


## Final gate numerical-token and renderer-identity fixes

Reginald found a remaining adversary: source2024:42kg could use the year token as both x and y. Numeric observation/derived-input token spans now must not overlap the exact matched category/coordinate label span in the same local record. Tests reject year2024 and Class2 label numbers reused as measurements, accept actual42kg token, and allow equal-valued coordinate/measurement only when the source provides distinct tokens. This applies to y/pie-values/z and derived inputs; numeric x coordinate proof remains independent.

Canonical provided trace UIDs now require nonempty ASCII letters/digits/underscore/hyphen and uniqueness within a spec. Unsafe JSON punctuation, spaces, Unicode, empty and duplicate IDs fail before rendering. This avoids Plotly purge invalid CSS-selector runtime failures seen in the real browser. Absent UIDs still use renderer-generated identities. Existing invalid saved visuals preserve usable answers with their explicit failed/unverified state.

Final same ten-file regression: **235 passed, 1 existing warning, 4.00s, exit0**. Prior225/95 checkpoints above remain valid historical integration evidence; the final two targeted fixes affect research verification/contract only, and were covered in the expanded clean235 run.


## Scientific numeric precision final review

Reginald reproduced nonzero1e-20 source values being accepted as0/1e-13 due to the earlier absolute tolerance, raw1e-324 underflowing to0, and unsafe integer tokens passing through rounded floats. Those weaknesses are removed. Actual raw evidence tokens compare as exact Decimal values against the declared input's round-trip decimal representation; nonzero underflow and integral tokens beyond2^53−1 fail regardless of whether the declared input is a Python int or float. Raw source tokens retain their original quote/index references.

Identity observations require exact decimal equality. Formulas are now evaluated using exact rational inputs and exact decimal conversion factors. Explicit rounding is rational decimal ties-to-even (e.g.2.675→2.68 at2places) and must be marked derived. Zero, integer, subnormal and explicitly rounded outputs are exact. Only nonintegral unrounded derived arithmetic permits at most one binary64 ULP around the correctly rounded exact result, allowing ordinary0.1+0.2 without authorizing zero corruption or a scale-independent tolerance floor. There is no absolute/relative isclose allowance.

Regression fixtures include all reported adversaries, safe-large identity plus100/+1000 corruption, valid small scientific observations through1e-323, valid floating arithmetic, bad derived values, explicit rounding and origin classification. No UI/schema/API changes were required for this numerical verifier fix.

Final precision-fix verification: same exact ten-file offline command above, **250 passed, 1 existing warning, 11.81s, exit0**. Prior95-test disposable integration result remains unchanged; final verifier-only changes are covered by this expanded250-test clean run.


Extreme exponent followup: Decimal parsing failures become safe visualization validation failures. Exact comparison to the finite declared input precedes all magnitude/exponent arithmetic; context-free copy_abs avoids Decimal context overflow. Extreme-exponent zeros normalize to Decimal(0) before Fraction construction. Regressions assert huge positive/negative source exponents never reach Fraction allocation; huge-zero exponents remain safely usable without constructing large denominators.

Latest clean exact same ten-file offline command: **259 passed, 1 existing warning, 11.28s, exit0** including extreme source exponent/allocation regressions.
