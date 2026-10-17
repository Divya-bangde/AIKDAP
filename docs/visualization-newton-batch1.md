# Newton — Batch 1 frontend evidence

15 October 2026. Scope: V02, V04 frontend integration, V05, V06, V10. Browser fixtures are synthetic/local; no import actions or existing projects were changed.

## Findings verified before changes

- `analysis-chart.ts` grouped traces used only `chart.y[0]`; multi-metric selection lost values. Series grouping coerced keys to strings.
- Pie traces normalized displayed rows without scope/validity checks. `total_rows` describes computed groups, not necessarily the full uncapped dataset.
- Lines retained supplied order without declaring trend/trajectory semantics or null policy.
- `ResearchResult.tsx` rendered only a non-null visualization; no separate visualization completion status existed.
- `PaperMap.tsx` rendered an empty state for fewer than two links, including one valid citation.

## Changes

- `analysis-chart.ts`: full metric × series mapping with stable typed identities; explicit metric panels whenever units are incompatible or unknown; no fabricated conversions. Shared axes require known equal units. Missing columns/row fields, nonnumeric/nonfinite values and over-budget data produce an actionable chart error while preserving table/answer.
- Ordering contract matches Nicolas: `metric_units`; optional `ordering` with trend numeric/date, ordinal domain, or trajectory. Trend/domain sorting occurs within groups and keeps every metric aligned. Dates require valid ISO calendar dates; duplicates are rejected unless a trajectory is explicitly declared. Null metric values remain null with `connectgaps:false`; missing axis values fail clearly. Legacy unspecified ordering is explicitly labelled and retains sequence.
- Pies require exactly one metric/no series, unique preaggregated categories, finite nonnegative values and finite positive totals; at most 50 categories. Denominator always says **Share of displayed categories**, with visible partial-result and unknown-source-scope explanations; no Other value is inferred. Loader cap metadata is integrated when supplied.
- `AnalysisCard.tsx`: visible notices and independent panels; preparation failure preserves narrative, computation explanation, and table.
- `AnswerVisualization.tsx`, `ResearchResult.tsx`: canonical `visualization_outcome` status/reason/requested intent/next action rendered independently in grounded, unsourced and insufficient-evidence branches. Unverified legacy visuals are withheld with an honest explanation. Origin/scope are visible.
- `PaperMap.tsx`: distinguishes no matched papers, no edges, one edge and multiple edges; one edge has a selectable relationship card plus graph. Isolated-node scope is explained separately.

## Tests

From `frontend/`:

`npm test -- src/features/business-analytics/analysis-chart.test.ts src/features/business-analytics/AnalysisCard.test.tsx src/features/paper-map/PaperMap.test.tsx src/features/research/AnswerVisualization.test.tsx src/features/research/ResearchResult.test.tsx --maxWorkers=2`

Exit **0**, **5 files / 53 tests passed**, Vitest 4.1.11, duration 6.62s. Covers bar/line 2×2 traces, metric panels/unit compatibility, stable CSS-safe identities, missing/nonfinite/null values, numeric/date/domain/trajectory ordering, absent ordinal categories as null gaps, duplicates/invalid dates, partial/invalid/ambiguous pies, zero/one/many citation edges, legacy withholding, verified output, nonquantitative diagrams, referenced evidence controls and unmet intent.

Integration review corrections: Nicolas supplies `result.scope={partial,dataset_truncated,label,description}`, now consumed beside charts. Produced diagrams can have `verification=unverified` because numeric point verification does not apply; numeric charts still require verification. Root's real-browser test found Plotly purge interpreted JSON uid punctuation as a CSS selector, causing unmount failure; uid now uses a collision-free hexadecimal encoding with a regression requiring CSS-safe characters.

Reginald's material review corrections: research visual provenance now exposes source controls wired to the existing source/PDF passage viewer plus a transformation disclosure. Only citation IDs both resolved in the current run and referenced by plotted provenance receive controls. Analytics limits match default research limits: 32 traces across panels, 10,000 points per trace, 50,000 total points, 500 characters per label, 2,000,000 serialized bytes; pie categories bounded to 50. At/above trace/point/label boundaries tested. Failed preparation keeps the table and narrative available.

Ordinal gap expansion is included in projected total/per-trace budgets before allocation, and domain labels participate in label-length limits. A sparse 32-series × 2,000-category domain cannot bypass the 50,000 point cap.

`npx tsc -b --pretty false` initially reported only the root-owned temporary audit harness unused React import. Root informed. Recheck after root removes that import.

Browser and performance evidence are maintained by root in the central progress document; mocked renderer unit tests establish component decisions, not actual Plotly/canvas fidelity or screen-reader usability.
