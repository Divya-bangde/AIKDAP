# Reginald — Batch 1 review

Review date: 15 October 2026. Status: Batch 1 gate approved after material findings were resolved and affected regressions/browser evidence reviewed.

## Review boundary

Read AGENTS.md, the complete visualization improvement prompt, the audit, historical probe/test logs, and current synthesis, analytics, research schema, paper map, persistence and client code. Historical measurements are investigation context, not current acceptance evidence. The numeric mismatch probe used an offline gateway double, not a live provider.

The checkout already contains extensive user changes. In particular `backend/app/modules/research/schemas.py`, research persistence/model/service files, worker files, API types and several frontend page files were dirty before this review. Findings below describe verified baseline behavior and must not be attributed to this implementation.

## Verified baseline findings

| ID | Current behavior | Required acceptance evidence |
| --- | --- | --- |
| V01 | `GroundedSynthesizer.synthesize` retains a parsed chart when answer grounding is `grounded`, without independent value verification. | Reject 42/999999 while retaining supported prose; bind every plotted numeric value to authorized supplied evidence and transformations; reject foreign/mismatched source identity; carry resolved evidence into the response. |
| V02 | `chartFigure` uses `chart.y[0]` inside each series group. | Four correctly mapped metric/group combinations; grouped bars and lines; missing fields fail visibly; nulls stay gaps; incompatible units use panels or explicit rejection. |
| V03 | `Visualization` accepts any nonempty list in trace metadata; arbitrary layouts pass through. | Typed supported trace union; finite numeric validation; paired lengths; rectangular grids and dimension compatibility; allowlisted layout; tests at and above trace, point, grid, label and byte budgets. |
| V04 | Point-list repair infers line from numeric x; output intent/status are absent and invalid legacy charts disappear silently. | Explicit intent preserves yearly bars; independent completion statuses persist end to end; legacy provenance remains unknown; bounded repair cannot repeat unrelated stages. |
| V05 | Pie normalizes the displayed limited rows; scope disclosure is collapsed below the chart. | Visible subset denominator; no row-count-derived Other; finite nonnegative positive total; avoid silently losing extra metrics or series. |
| V06 | Analytics line construction preserves result order, with no trend/path distinction or duplicate/gap policy. | Stable chronological/domain sorting per group with aligned metrics; explicit trajectory retention, duplicate/date/gap handling. |
| V10 | `PaperMap` treats `links.length < 2` as no citations. | Zero papers, zero citations, one valid edge, and many edges have truthful separate behavior; one edge remains selectable. |

## Integration and security review conditions

- Citation membership must be checked against supplied context. A valid citation ID must not authorize arbitrary asset/chunk references or values, and matching a number anywhere in a passage does not establish category/unit association.
- `_resolve_full_evidence_text` currently trusts owner/project-scoped upstream retrieval and loads chunks by ID. Changes must preserve or strengthen that scope boundary rather than accept model-supplied chunk identifiers for database lookup.
- Validate metadata/units transformations against verified inputs. Model assertions of measured/derived/illustrative origin must not promote illustrative source values to measurements.
- Persist independent intent/status/reason/next action through synthesis result, graph state, research service/model, API read path and client types. Unsupported visuals must preserve a useful textual answer.
- Stable trace identity should use typed metric/group keys; string coercion alone can collapse `null`/`"null"` or numeric/string categories.
- A jsdom force-graph replacement with HTML buttons proves callback behavior only. Production canvas/list selection and one-edge rendering need real-browser evidence.
- Existing analytics tables and computation explanations, source passage access, safe equation evaluation, reference curves, restrictive Mermaid and non-trusting KaTeX must remain intact.

## Gate result

Independent reviewer regression command from `backend/`:

`.\.venv\Scripts\python.exe -m pytest tests/test_visualization_contract.py tests/test_analytics_plan.py tests/test_analytics_executor.py tests/test_grounded_synthesis.py -q`

Exit **0**, **106 passed**, one LangGraph pending-deprecation warning, reported duration **3.77 seconds**. This verifies deterministic code with synthetic fixtures, not live provider behavior.

Material implementation findings and owner resolutions:

1. **V01 category/value reassignment and origin promotion:** direct isolated probe supplied `Illustrative sample: East 10 kg; West 42 kg.` and charted East=42 as measured; original new verifier returned verified. Nicolas added conservative local observation binding and source-origin override, with independent regressions. Those tests now pass. Follow-up exact category-boundary edge (`East` versus `Northeast`) sent to Nicolas.
2. **V03 explicit axis compatibility:** new contract initially accepted category coordinates on linear axes and negative observations on logarithmic axes. Nicolas now rejects semantic conflicts for 2D and 3D axes; four regressions pass.
3. **Diagram integration:** numeric verification is inapplicable to diagrams, but the initial frontend visibility gate hid them. Newton added a kind-aware exception and regression.
4. **V01 visual evidence access:** frontend initially exposed only general answer citations. Newton added source evidence controls filtered to visual provenance and connected to existing authorized source viewer/web/evidence drawer behavior.
5. **Analytics renderer budgets:** initial total point cap still allowed 1,000 distinct traces. Newton added aligned default trace/point/label/byte limits, plus a projected total including inserted ordinal gap positions. Tables remain usable on rejection; boundary and expanded-domain tests pass.
6. **Unit metadata trust:** arbitrary model `metric_units` could invent units or combine incompatible metrics. Nicolas clears model unit assertions because the dataset profile has no trusted units; unknown units use separate panels.

The frontend mapping, panel separation, null gaps, declared trend/domain/trajectory ordering, visible pie denominator, typed stable trace identities and single-edge relationship card were inspected. Root's browser found punctuation in Plotly `uid` caused selector failure on purge; Newton replaced it with CSS-safe hexadecimal identity and added regression.

Independent reviewer frontend command from `frontend/`:

`npm test -- src/features/business-analytics/analysis-chart.test.ts src/features/business-analytics/AnalysisCard.test.tsx src/features/paper-map/PaperMap.test.tsx src/features/research/AnswerVisualization.test.tsx src/features/research/ResearchResult.test.tsx --maxWorkers=2`

Exit **0**, **53 tests / 5 files passed**, duration **4.47 seconds**, clean exit. No remaining material frontend code review blocker for Batch 1; real-browser source-access/renderer evidence remains root-owned.

Additional backend integration review requests were resolved: missing outcome on no-supplied-evidence/extractive synthesis; explicit unsupported requests accepting another trace type; exact category boundaries; z/grid values associated with point/cell coordinates. Illustrative source qualifiers are checked across the actual source chunk rather than only beside a selected quote. A final malformed-output finding (`data=[1]` or `[None]` previously raised before the failure handler) was fixed with a dict guard and regression.

Final independent reviewer backend command from `backend/`:

`.\.venv\Scripts\python.exe -m pytest tests/test_visualization_contract.py tests/test_analytics_plan.py tests/test_analytics_executor.py tests/test_grounded_synthesis.py tests/test_research_web_and_visuals.py -q`

Exit **0**, **140 passed**, one LangGraph pending-deprecation warning, **4.10 seconds**. This includes service completion/read status persistence with a deterministic session double, supported prose preservation when a chart is withheld, malformed visual entries, unsupported requests, source scope, dimensional/axis validation and budget boundaries. It does not establish a live database migration or live-model accuracy; those need separate evidence.

Root supplied current browser evidence in `docs/visualization-evidence-2026-10-15/batch1-browser.json`. Reviewed the actual screenshots `batch1-analytics-synthetic-1440x900-light.png`, `batch1-one-citation-selected-synthetic-1440x900-light.png`, and `batch1-evidence-drawer-synthetic-1440x900-light.png`. The two metric panels visibly preserve all four metric/region traces, chronological month alignment and the East gap; one citation is selectable through the production detail sheet; visual evidence opens the real evidence drawer and displays the source 42 kg. Root also checked numeric yearly bars, invalid/partial pies, zero/no/multiple citation states, and missing visual status without uncaught browser errors. Environment: Chromium 153.0.8010.12, Vite dev server, real production React/Plotly/canvas components, synthetic fixtures, 1440×900 light theme. Production-bundle performance and broader accessibility are later batch requirements.

**Gate: approved to proceed to Batch 2.** No remaining material Batch 1 code review findings. Preserve the conservative verification limitations: ambiguous evidence is withheld, legacy provenance is unknown, units are not invented, and deterministic fixture verification does not certify live-model accuracy or whole-site accessibility.

## Reviewed V06 research follow-up

Nicolas extended declared trend handling to research traces: numeric/date/ordinal/calendar-month order, intentional trajectory preservation, date/timezone/duplicate checks, aligned x/y/z and remapped point provenance. Independent reviewer contract run before the final two follow-up regressions: `python -m pytest tests/test_visualization_contract.py -q`, exit **0**, **65 passed**, one warning, **2.24 seconds**. Owner reruns the expanded suite afterward.

Follow-up closed after Nicolas's final expanded nine-file regression: **190 passed**, one existing warning, **exit 0**, exact command and contract/type-generation details in `visualization-nicolas-batch1.md`. This is additional backend evidence after the original Batch 1 checkpoint, not a replacement for its recorded browser checks.

Requested and reviewed the missing declared-domain gap correction: research charts with an absent interior ordinal category are withheld with an explicit request for null observations, rather than bridging a known gap or inventing source-backed observations. A supplied explicit null row remains a gap and sorts correctly. Deterministic analytics may insert declared-domain null positions into its view; these are labelled missing values, not measurements. Incidental words such as bar pressure and surface tension no longer constitute visual requests, and explicit line requests take precedence over such words.
