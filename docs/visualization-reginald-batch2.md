# Reginald — Batch 2 review

Review started 15 October 2026; continued 16 October 2026 after resume. Gate: **approved to proceed to Batch 3** after material findings were resolved and current tests/browser artifacts reviewed.

Batch 1 gate and its limits remain recorded in `visualization-reginald-batch1.md`. Batch 2 covers V07–V09 and V11–V12. Existing user changes remain outside review attribution.

## Acceptance review checklist

| Item | Required evidence |
| --- | --- |
| V07 | Shared meaningful chart frame; honest finding/origin/units/source/scope/filters; accessible table describes actual plotted traces including inserted ordinal gaps and grid coordinates; deliberate exports carry context. Original full analytics table/computation explanations remain available. |
| V08 | Production searchable list selects every map paper, including isolated nodes; source and import controls are keyboard reachable; closing details restores predictable focus; labelled fit/reset/zoom/pause controls; direction/node-size explanation. Real browser verification does not activate imports. |
| V09 | Contrast calculations against actual adjacent light/dark backgrounds for essential marks; persistent redundant encoding across reordering/filtering; grayscale and common color-vision simulation inspection; no whole-site certification claim. |
| V11 | Slider name and aria-valuetext expose physical values/units, with synchronized numeric inputs; logarithmic scale/domain failures are clear; reference curves and stable/widen-only axes preserved. |
| V12 | Concise honest visual run title/full-question disclosure; visual precedes long explanation with essential qualifications visible; readable diagram detail and relationship alternative; 1440×900 and 390×844 plus 200% zoom/touch/keyboard; settled overflow stays in labelled local containers. |

## Baseline issues verified for owners

- `PaperMap` has only canvas node actions apart from the single-edge relationship card; no synchronized all-paper keyboard path or explicit viewport controls yet.
- `graph-colors.ts` uses `--border-strong` for citation edges/arrows, making relationship contrast a separate issue from chart series colors. Root owns graph color correction and contrast evidence.
- Plotly has transparent backgrounds. Contrast must account for analytics card, research frame `bg-sunken/40`, and equation article `bg-background`, in both themes.
- Numeric equation inputs accept arbitrary finite values and stretch ranges. A nonpositive value can silently change a logarithmic slider to linear; owner must handle/report domain or scale changes clearly.
- `ResearchRunView.tsx` uses the full run query for the page heading, while `ResearchResult` puts lengthy answer prose before the requested visual.

No Batch 2 gate approval is inferred from Batch 1 tests or renderer mocks.

## Implementation review findings

1. **V07 visible data synchronization:** the initial shared frame controls series selection, but Plotly's default legend actions could independently hide traces. The accessible table and contextual export would continue claiming to describe visible traces while including hidden series. Requested Newton either disable independent legend toggles and expose shared selection controls, or synchronize renderer visibility events into frame state. Root notified to check real legend interaction.
2. **V09 scope of redundant encoding:** inspected root's stable uid/name hashing, dashed lines, marker shapes and bar/pie patterns. Finite encodings may collide for arbitrary many-series inputs; accessible labels and shared series selection remain necessary. Legacy unnamed traces use positional fallback and must not be described as possessing a known source identity.
3. **V07 brief finding:** research and analytics initially never supplied the frame's optional finding. Requested a concise deterministic summary of actual plotted values or a trusted finding, without promoting unverified narrative numbers.
4. **V12 oversized diagram detail:** the initial 10,000px natural-width clamp could shrink labels for a long allowed diagram. Requested preservation of natural width in the local scroll view, or an explicit geometry-budget failure with a text alternative.
5. **Experiment context and fidelity follow-up:** expected test-case outputs need an honest origin label and source/result metadata. Numeric aliases such as `1`/`1.0` need the duplicate-coordinate policy; unsafe large integer strings must not silently become rounded JS/float coordinates. Requested targeted canonical backend fixes and frontend context consumption.

Items 3–4 are resolved in Newton's current source: deterministic per-trace displayed ranges/counts appear beside the chart and in export, and diagrams retain their actual natural width in a local scrolling region. Unique-name/UID selection persists across trace reorder; positional fallback remains only for anonymous/duplicate-name legacy data. Exact plotted tables now use numeric default grid indices. Focused tests cover inert untrusted export titles/source identifiers, gap/pie/grid values, selection/reorder, pagination/export failure and real paper-list HTML search/keyboard/focus behavior.

Additional V09 finding after examining equation traces: reference opacity 0.35 and comparison-family opacity 0.25–0.8 produce subthreshold alpha-composited essential curves even when the raw accent passes. Requested root/Newton preserve reference/comparison semantics through dash/width/markers while correcting effective contrast. Root's long-label/200% browser checks also found clipped/overlapping Plotly labels; root owns the minimum-readable-width/local-scroll/tick-label fix before gate approval.

The equation contrast follow-up is resolved in current code: reference/comparison opacity is 1, and the five comparison curves receive explicit distinct dash styles. Root's Plotly view now supplies a labelled local horizontal scroll area and minimum readable plot width, and abbreviates long renderer tick labels while retaining exact values/context/table/export. Final browser rechecks remain required.

The independent legend toggle issue is resolved in current `themedLayout`: independent item click/double click toggling is disabled, and frame selection controls are available for every multi-series chart. Source inspection confirms searchable paper controls share the production details/source/import path, restore focus to a connected opener or search, explain global citation size and preserve the map's scope while searching.

## Current independent regressions

From `frontend/`:

`npm test -- src/features/research/AnswerVisualization.test.tsx src/features/business-analytics/analysis-chart.test.ts src/features/business-analytics/AnalysisCard.test.tsx src/features/paper-map/PaperMap.test.tsx src/features/research/ResearchResult.test.tsx src/features/research/ResearchRunView.test.tsx src/features/research/ExperimentVisualizationChart.test.tsx src/features/research/plotly-theme.test.ts --maxWorkers=2`

Exit **0**, **73 passed / 8 files**, **7.17 seconds**.

`npm test -- src/features/research/AnswerEquations.test.tsx --maxWorkers=2`

Exit **0**, **28 passed / 1 file**, **4.04 seconds**. The new tests check physical kilogram announcements, synchronized numeric/slider values and rejection of nonpositive log-domain inputs, alongside preserved safe evaluation/reference behavior. These are unit/component regressions, not real Plotly or screen-reader certification.

Requested additional focused frame/export/table and paper-list keyboard/focus regressions from Newton. The earlier typecheck log reports a test fixture's numeric x instead of canonical string x and unsupported role-query `exact` option; Newton owns these straightforward test corrections.

Follow-up focused reviewer command: `npm test -- src/features/research/ChartFrame.test.tsx src/features/paper-map/PaperMap.test.tsx src/features/research/ExperimentVisualizationChart.test.tsx src/features/research/AnswerVisualization.test.tsx --maxWorkers=2`, exit **0**, **23 tests / 4 files**, **4.48 seconds**, before the final reorder/finding wording additions. Newton's complete eight-file run afterward: **84 tests**, **exit 0**, **9.97 seconds**; exact command in `visualization-newton-batch2.md`.

Backend independent reviewer command from `backend/`:

`.\.venv\Scripts\python.exe -m pytest tests/test_visualization_contract.py tests/test_grounded_synthesis.py tests/test_research_web_and_visuals.py tests/test_analytics_executor.py tests/test_analytics_plan.py tests/test_analytics_graph.py tests/test_analytics_grounding.py tests/test_analytics_dataset.py tests/test_claim_wiring.py tests/test_experiment_visualization_bounds.py -q --tb=short`

Exit **0**, **225 passed**, one existing LangGraph warning, **4.53 seconds**. Nicolas additionally reports clean disposable PostgreSQL/Redis integration: **95 passed**, **exit 0**, no skips, both containers removed, plus canonical API generation exit 0. Exact commands and limits are in `visualization-nicolas-experiment-followup.md`. Reviewed expected-only origin/plan-version/scope/source metadata, stable source/case sorting, finite/duplicate/unsafe numeric preflight and actual route 422. Analytics now retains native float precision and encodes unsafe integers as exact strings with explicit chart-withholding metadata. No remaining material backend review blocker.

## Production interaction/mobile evidence reviewed

Reviewed `batch2-paper-map-browser.json` and `batch2-paper-map-accessibility-tree.yml`: Chromium 153.0.8010.12, 1440×900 synthetic cached data, real production canvas and HTML controls. Source link and existing import button are reachable by keyboard; import was not invoked, pause state is exposed, Escape restores the exact opener, all four papers including isolated/outside nodes appear in the tree, and errors are empty.

Viewed `batch2-equation-synthetic-375x844-light.png`, `batch2-diagram-touch-synthetic-390x844-light.png`, and `batch2-research-visual-first-synthetic-390x844-light.png`: visible logarithmic policy and physical controls; readable local diagram scrolling/zoom with relationship text alternative; concise visual title/full-question disclosure and origin/scope/evidence preceding the chart and long answer. Remaining long-label/200% and contextual-download verification stays pending root's final evidence.

## Final current code checks before browser gate

Independent reviewer command from `frontend/`:

`npm test -- src/features/research/ChartFrame.test.tsx src/features/paper-map/PaperMap.test.tsx src/features/business-analytics/analysis-chart.test.ts src/features/business-analytics/AnalysisCard.test.tsx src/features/research/AnswerVisualization.test.tsx src/features/research/ResearchResult.test.tsx src/features/research/ResearchRunView.test.tsx src/features/research/MermaidDiagram.test.tsx src/features/research/AnswerEquations.test.tsx src/features/research/ExperimentVisualizationChart.test.tsx src/features/research/plotly-theme.test.ts --maxWorkers=2`

**110 passed / 11 files**, **exit 0**, **12.98 seconds**, clean exit without uncaught asynchronous errors.

Independent `npx tsc -b --pretty false`: **exit 0**.

Reviewed root's experiment UI consumption: expected-only/unknown legacy origin, plan/version/selections/scope, aligned case/source metadata in contextual exports, authorized asset lookup for source opening, and saved-reference fallback on source load failure. Numeric exact-string analytics results retain the table and show explicit withheld-chart feedback. No remaining material backend/frame/keyboard/physical-control code finding; final long-label/zoom and actual contextual browser-download artifacts are pending.

## Final browser/export gate

Reviewed `batch2-browser.json` from the isolated **production fixture build**, Vite **45.33 seconds**, exit 0, Chromium 153.0.8010.12, synthetic data with no user project writes. Selecting 11 of 12 series produces the same 11 renderer traces and 33 body-table values; independent legend toggling is disabled and a real legend click does not change visibility. Both initial and recovery contextual downloads succeeded. Injecting a throwing `URL.createObjectURL` reports export failure while preserving the frame/answer/data, and restoring the API permits an actual successful download.

Inspected `batch2-verified-chart-data-and-context.json` and its recovered counterpart: title, displayed-value finding, illustrative origin, honest unknown scope/time, kg axis/unit context, source reference, run/project/question/requested intent, point provenance, exact bar 42 and plotted-value row remain present. This is a synthetic renderer artifact, not a live analytics/model result. Image/SVG export behavior remains a Batch 3 requirement.

Visually reviewed `batch2-series-selection-table-synthetic-1440x900-light.png`, `batch2-context-export-failure-synthetic-1440x900-light.png`, `batch2-long-labels-synthetic-375x844-light.png`, and `batch2-long-labels-200percent-css-zoom-synthetic-375x844-light.png`. Long labels/context wrap; chart scrolling stays in a labelled local container with a 420px readable plot; exact labels remain in tables/exports. At 200% CSS root zoom with actual layout reflow, document client/scroll widths both remain 375px and the local chart region reports 130px client / 420px scroll. This is documented as CSS zoom, not device-pixel-ratio simulation. Diagram: document 390/390px, local region 330/800px. Research chart precedes prose (plot top approximately 686px, prose approximately 1203px). Equation physical announcement is `1 kilogram; logarithmic scale`. Final recorded browser errors are empty.

Reviewed display-only axis/title shortening helper; original data and exported layouts remain unchanged. Independent affected command `npm test -- src/features/research/plotly-display.test.ts --maxWorkers=2`: **2 passed**, **exit 0**, **1.15 seconds**. This supplements the 110-test independent sweep above.

**Batch 2 approved.** No remaining material Batch 2 review finding. Preserve stated limits: targeted synthetic/browser accessibility checks do not certify every screen reader/device; finite style encodings may collide; unknown legacy origin/scope remain unknown; safe conservative numeric refusal may require future supported scaling. Detailed renderer recovery, image/SVG exports, live equation theme and measured optimization remain Batch 3 scope.

## V09 evidence review

Reviewed `docs/visualization-evidence-2026-10-15/batch2-contrast.json`: actual browser-resolved light/dark card, sunken, page background and research 40%-sunken composite, all eight series colors and graph node/edge colors. Lowest essential-mark ratio is **4.506:1 light** and **3.813:1 dark**, above the applicable 3:1 target. Citation links now use muted foreground rather than the low-contrast border token.

Visually inspected `batch2-analytics-grayscale-synthetic-1440x900-light.png`, `batch2-analytics-deuteranopia-synthetic-1440x900-dark.png`, and `batch2-analytics-protanopia-synthetic-1440x900-light.png`. Dashes, markers and legend labels distinguish the fixture series without depending on original hue. Root also captured the complementary theme/simulation combinations. These are targeted synthetic-renderer checks, not whole-site accessibility certification or a guarantee against all finite-style collisions.

No remaining material V09 blocker in the current implementation. Other Batch 2 items remain pending.
