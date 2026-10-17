# Visualization improvement progress — 15 October 2026

All three batches run sequentially. Existing user changes are preserved. Synthetic fixtures never import papers into a user project. Browser evidence is renderer verification, not a live-model accuracy claim.

## Gates

| Batch | State | Required gate |
|---|---|---|
| 1 — trust/correctness | Approved; ordering/precision/experiment follow-ups reviewed | Nicolas + Newton regressions, real Plotly/map browser checks, Reginald review |
| 2 — interpretation/access | Approved | Frame, production keyboard path, contrast, sliders/mobile checks and review |
| 3 — recovery/performance | Approved — 17 October 2026 | Theme, injected errors, contextual exports, production performance measurements and review |

## Acceptance tracker

| Item | Owner | State | Files / evidence / limitations |
|---|---|---|---|
| V01 independent plotted-value verification | Nicolas | Approved | `planner/visualization.py`, synthesis, authorized evidence resolver; 42/999999, category/cell swaps, conversions, foreign context and illustrative-origin regressions; source opening screenshot |
| V02 every metric and series | Newton / Nicolas | Approved | `analysis-chart.ts`, analytics plan; four real traces in metric panels; missing/null/unit tests |
| V03 typed bounded chart validation | Nicolas | Approved | `visualization_contract.py`, experiment schemas/preflight; typed finite arrays/grids/dimensions/limits and exact precision; final259 offline + prior95 disposable integration tests |
| V04 intent and output status | Nicolas / Newton | Approved | `visualization_outcome` migration028, service/state/read, canonical API types, `ResearchVisualization`; saved unknown/unsupported/missing intent tested |
| V05 pie denominator | Newton / Nicolas | Approved | `analysis-chart.ts`, result.scope; displayed-category denominator visible in browser, positive finite bounded validation |
| V06 ordering semantics | Newton / Nicolas | Approved | Analytics ordering + research provenance-remapped numeric/date/domain ordering; trajectories and explicit gaps preserved; ambiguous missing domain entries withheld |
| V07 shared inspectable frame | Newton / root | Approved | `ChartFrame.tsx`, research/analytics/equation/experiment integrations; exact table/selection/source/context JSON exports and failure recovery |
| V08 keyboard paper map | Newton | Approved | `PaperMap.tsx`; real canvas plus searchable HTML controls, source popup, import reachability without activation, focus restoration, labelled fit/zoom/pause; actual aria tree saved |
| V09 contrast and non-color encoding | Newton / root / Reginald | Approved | `index.css`, `plotly-theme.ts`, graph colors, equation reference/compare opacity1; actual contrast and grayscale/CVD evidence, stable styles/selection regressions |
| V10 graph empty states | Newton | Approved | Zero/one/many states, isolated-paper explanation; one actual relationship selectable |
| V11 physical logarithmic controls | Newton / root tests | Approved | `InteractiveEquationCard.tsx`; 1 kilogram announcement, scale explanation, numeric synchronization/domain errors; 28 equation tests and 375px browser screenshot |
| V12 mobile visual tasks | Newton / root | Approved | `ResearchRunView`, `MermaidDiagram`, Plotly display-only labels/labelled420px scroll; actual390/375px touch, keyboard, CSS200% reflow and compact visual-first screenshots |
| V13 live equation theme | Newton | Approved | `plotly-view.ts`, live equation styling; real2kg input/manual2D zoom/current-reference curves preserved; actual3D camera survives theme; screenshots/JSON |
| V14 renderer recovery / exports | Newton | Approved | Local async/load boundaries, bounded retry, exact data/context SVG/JSON; three injected chunk failures recover on reload; zero detached-resize errors |
| V15 accurate pipeline | Root as Newton / Nicolas verification | Approved | `LandingStory`, `PipelineRail`, `ProviderRouting`; conceptual ingestion/query paths and optional work; production1440/390 browser checks,9 landing tests |
| P01 supported 2D/3D lazy bundles | Newton | Approved | Five2D/two3D registry, lazycore+3D; actual app447.794kB gzip2D+CSS/wrapper,171.591kB separate3D; actual traces checked |
| P02 mathjs / Mermaid imports | Newton | Approved | Number-only mathjs116.618kB gzip retains AST allowlist; full lazyMermaid160.837kB preserves grammars/security, slight growth documented |
| P03 complexity and full data | Nicolas / Newton | Approved | `trend-reduction.ts`: declared numeric/date trends only, labelled original-point bucket min/max; actual5000→912 retains extrema/gaps; exact full5000-row JSON + mapping;31tests0 |
| P04 container resize | Newton | Approved | Guarded ResizeObserver, hidden/detached lifecycle; actual container1054→602, zoomed React navigation zeroerrors and375/390 touch checks |
| P05 runtime performance | Root / Reginald | Approved | Final interleaved3+3 matched samples: initial median8322→1709ms; equation1952→1038ms; separate V8 traces; full ranges/limits in results report |

## Evidence and commands

Evidence directory: `docs/visualization-evidence-2026-10-15/`. Original audit evidence remains in `docs/visualization-evidence-2026-10-14/`.

The current working tree was inspected before implementation. `initial-working-tree.txt` and `user-working-tree.patch` capture the initial user work (snapshot taken while owners were still investigating). No reset/stash/checkout is used.

Exact test commands, exit codes, screenshots, schema/migration notes, measured comparisons, review findings and residual limitations will be appended at each gate.

## Batch 1 checkpoint

Reginald approved the gate after independent **140 backend tests** and **53 frontend tests**, both exit **0**. See `visualization-reginald-batch1.md`, `visualization-newton-batch1.md`, and `visualization-nicolas-batch1.md` for exact affected-file commands and review resolutions. Root's frontend command (same five files, `--maxWorkers=2`) is saved in `batch1-frontend-tests.log`: **53 passed, exit 0, 6.14s**. `npx tsc -b --pretty false`: **exit 0** after repairing the isolated fixture's syntax/import.

Current-source browser checks use Chromium **153.0.8010.12**, viewport **1440×900**, light theme, synthetic fixtures and the actual production components served by Vite. `batch1-browser.json` and labelled `batch1-*.png` establish:

- Four metric/region traces in separate panels, January→February→March order and aligned null gaps.
- Numeric years remain bars with category axes; partial pie explicitly uses displayed categories.
- Zero citations and no matched papers have distinct messages; one relationship opens the actual paper detail sheet; multiple links mount the canvas.
- The verified synthetic chart's evidence button opens the existing evidence drawer showing **42 kg**.
- Missing chart output and invalid pie values leave prose/table available. Navigation/unmount produces no uncaught browser errors after the CSS-safe trace-ID repair.

Contract: typed allowed traces/layout, configurable `VISUALIZATION_MAX_*` budgets, separate `visualization_outcome`, deterministic point verification, strict authorized source matching, declared origin and scope. Nullable JSONB migration **028** preserves legacy charts/answers while legacy metadata remains unknown; unverified numeric charts are withheld on read. Canonical OpenAPI client types regenerated. `backend/.venv/Scripts/python.exe scripts/verify_visualization_migration.py`: **exit 0**, disposable Docker PostgreSQL upgrade/downgrade/re-upgrade and outcome round-trip; fixture container removed. No user database was migrated.

Review resolved category/value swapping, illustrative promotion, invalid linear/log axes, unsupported intent substitution, malformed trace elements, grid coordinate reassignment, arbitrary unit metadata, ordinal expansion budgets and Plotly purge failures. The research-specific ordering follow-up is complete: values and provenance are reordered together, trajectories/null gaps remain, and missing declared domain entries are withheld until explicit gaps are supplied. The experiment/precision follow-up is reviewed in `visualization-nicolas-experiment-followup.md`: **225 offline tests, exit0**, and **95 disposable PostgreSQL/Redis integration tests, exit0 with no skips**, canonical type regeneration0. Unsafe integer results stay exact in tables and carry a chart withholding reason; expected experiment values cannot imply observed execution results.

## Batch 2 current evidence

V09: light palette adjusted, graph edges use the existing muted foreground token, and traces gain stable identity-based dashes, marker shapes, bar/pie patterns and outlines. `npm test -- src/features/research/plotly-theme.test.ts --maxWorkers=2`: **6 passed, exit 0**; `npx tsc -b --pretty false`: **exit 0**. The actual CSS token calculation in `batch2-contrast.json` checks all eight palette marks plus graph nodes/edges against card, sunken, page and research's 40% sunken/card composite. Minimum: **4.506:1 light**, **3.813:1 dark** (all exceed the applicable 3:1 threshold).

Labelled grayscale, deuteranopia and protanopia approximation screenshots were captured in both themes and visually inspected. These are limited chart checks, not whole-site accessibility certification. Hash-based encodings can collide; shared series selection provides inspection when many series are present. Named/UID traces remain stable across filtering/reorder; anonymous legacy traces have no source identity and fall back to index.

## Batch 2 checkpoint — resumed 16 October 2026

Reginald approved the batch after reviewing code, actual renderer screenshots, accessibility tree, contextual downloads and failure/recovery. `visualization-reginald-batch2.md` records **110 tests across 11 files**, plus **2 display-label regressions**, exit0, and typecheck0. Root's 10-file affected suite is saved as `batch2-all-frontend-tests.log`: **109 passed, exit0, 10.77s**. Root's final style/display/equation sweep is `batch2-final-root-tests.log`; all passed, exit0. The later distinction repair keeps equation live curves solid while their reference stays dotted; reference/comparison alpha is1 (see `batch2-equation-contrast.json`).

The Batch2 isolated production fixture build (`npx vite build --config visualization-check.config.ts`, `VISUALIZATION_OUT_DIR=../docs/visualization-evidence-2026-10-15/batch2-dist`) completed **45.33s, exit0**. `batch2-browser.json` and `batch2-paper-map-browser.json` record actual Chromium checks. Source navigation was exercised by keyboard; the synthetic DOI response was locally intercepted, so this proves the navigation/control path, not live paper availability. No import action was activated.

`batch2-verified-chart-data-and-context.json` is an actual browser download retaining title, exact42kg value, original axis units, illustrative origin, unknown scope notice, citation reference, run/question, outcome and point provenance. Injecting a `URL.createObjectURL` failure preserves the frame/data and reports a status; restoring it enables `batch2-recovered-chart-data-and-context.json`. Twelve-series selection removes Series2 from actual Plotly traces/table; independent legend toggles are disabled, and clicking the legend leaves the same11traces.

Mobile checks use touch-enabled390×844 and375×844 contexts. CSS root zoom2 produces actual reflow at200% rather than only increasing pixel density. The page remains390/390 or375/375 client/scroll width. A readable diagram scrolls locally (330px region/800px content after zoom); narrow charts keep420px content inside a labelled local region. Long display labels/titles are abbreviated with explicit explanation while full values/context/export metadata remain intact. Full research view has a concise title/full-question disclosure; the chart begins before the long answer, with origin and evidence access retained.

A resize/unmount asynchronous error appeared while navigating between zoomed plots after these checks. It is explicitly assigned to V14/P04 in Batch3 and must have a clean final browser regression before completion.

## Current-source performance baseline

`npm run build` from `frontend`: **exit 0**, Vite **57.00s**, before renderer optimization. Raw/gzip kB: Plotly **4279.53/1324.70**, equation **682.61/197.29**, Mermaid **659.95/159.90**. Build time is not runtime performance.

The isolated fixture production build (`npx vite build --config visualization-check.config.ts`, exit 0) uses the unchanged baseline renderer, frozen in `baseline-dist` while implementation proceeds. It captured an in-progress analytics mapping revision; runtime measurements select the unchanged simple research plot before analytics mounts, so the measured data/renderer path is isolated. Three fresh-context samples use Chromium153, **4× CPU slowdown, 10 Mbps down / 5 Mbps up, 40ms latency, gzip transport, cache disabled**, 1440×900. `baseline-runtime.json`: median initial research renderer **8479ms**, additional equation load/render **2072ms**, equation keyboard action + two frames **361ms**, Mermaid cold load/render **590ms**. Every sample has zero page errors.

`benchmark.cjs` records the procedure. `baseline-compile.json` separately records V8 parse/compile trace-event durations under the same profile. Inclusive/nested trace-event totals overlap and must not be added together. Agreed target for the ordinary 2D renderer is **≤600 kB gzip**, while retaining bars/scatter/pies/heatmaps and loading 3D separately; equation target **≤150 kB gzip** subject to preserving the safe evaluator. Retained Mermaid size is acceptable if preserving its supported diagram types and lazy sub-renderers is necessary. Runtime gains must be reported as measured observations, including regressions and uncertainty.

## Final checkpoint — all batches approved, 17 October 2026

Reginald approved Batch3 after source review, independent affected tests, actual browser/download evidence and final matched performance review. See [visualization-reginald-batch3.md](visualization-reginald-batch3.md). All V01–V15 and P01–P05 acceptance work is complete; no material blocker remains. The detailed handoff is [visualization-improvement-results.md](visualization-improvement-results.md), including changed files, exact commands, migration/API notes, labelled screenshots, actual exports, runtime/bundle comparisons and practical limitations.

Final source verification:

- Backend ten-file regression: **259 passed, one existing LangGraph pending-deprecation warning,6.51s, exit0** (`final-backend-tests.log`). Exact command is in the results report. The earlier **95-test disposable PostgreSQL/Redis integration** gate passed without skips; no later DB/schema change occurred. Migration028 upgrade/downgrade/re-upgrade/JSON round-trip passed against a disposable database, exit0; no user database was migrated.
- Full frontend: `npm test -- --maxWorkers=2` from `frontend`: **65 files /405 passed,62.63s,exit0**, no warnings (`final-frontend-tests.log`). This includes the final3D controls/view retention and display-reduction regressions; the earlier396-test run is historical.
- Actual application: `npm run build` from `frontend`: **tsc-b + Vite26.37s,exit0** (`final-application-build.log`). Final clean isolatedQA/performance fixtures passed **25.05s/23.77s,exit0** using `--emptyOutDir`; these build times are not runtime measurements. Existing Vite large-chunk notices remain documented, while renderer-specific gzip budgets pass.
- Actual browser scripts `final-browser-check.cjs`, `final-contract-browser.cjs`, `final-recovery-check.cjs`, `final-large-trend-check.cjs`, `final-landing-check.cjs` and `diana-3d-diagnostic.cjs`: **exit0**. JSON/log/screenshot artifacts are in the evidence directory. Normal checks have zero unexpected page errors; intentional chunk failures are recorded separately. Explicit software WebGL was used for reproducible3D mouse orbit.

Final review closed issues found during verification: Decimal/Fraction scientific-value acceptance replaced unsafe tolerance floors;3D Fit/Reset use scene-only updates; public camera state survives theme updates; native resizing ignores hidden/detached targets; renderer retries are bounded and reload recovery is verified; exact data/source alternatives survive failures. Four actual light/dark SVG downloads were opened and visually inspected with context and exact embedded metadata.

Large-series work is explicit and conservative. Only accepted numeric/date trend lines above1000 points use contiguous-bucket min/max original-point sampling. First/last points, global extrema and null-gap boundaries/neighbors remain. The actual5000-point fixture renders **912** original points with unchanged−999/+1234 extrema and preserved gap2000–2005, while an actual full-result JSON download retains all5000 rows and row mappings. Complex gaps retain the full accepted trace with an explanation. Bars, pies, trajectories and ordinal lines are never sampled; invalid/over-budget source data remains clearly withheld. Reginald independently reconciled every retained plotted point to the full downloaded result.

Final performance supersedes the earlier baseline-only checkpoint above. Same-session interleaved3baseline/3final fresh contexts, Chromium153,4×CPU,10Mbps/5Mbps,40ms,gzip,cache disabled,1440×900, with builds/tests finished:

| Measure | Baseline median (range) | Final median (range) |
|---|---:|---:|
| Initial research renderer |8322ms (8216–8556)|1709ms (1700–1848)|
| Additional equation module/render |1952ms (1869–2062)|1038ms (1018–1068)|
| Keyboard action +two frames |288ms (212–380)|139ms (132–173)|
| Mermaid cold module/render |647ms (541–1785)|602ms (579–645)|

All six samples have zero page errors (`matched-runtime-2026-10-17.json`, `matched-runtime-summary.json`, `final-performance.log`, executable `final-performance.cjs`). Initial-render median improved79.5% in this fixture; three samples are not a production-wide guarantee. Mermaid ranges overlap, so no dependable Mermaid speed gain is claimed. Separate inclusive V8.CompileCode **2010.586→329.910ms**, V8.ParseProgram **844.146→20.444ms**; these nested categories must not be summed.

Actual final application raw/gzip decimal kB: ordinary2D engine+requiredCSS+wrapper **1314.534/447.794**, additional lazy3D **570.331/171.591**, equation **396.107/116.618**, Mermaid **662.260/160.837** (`final-application-bundles.json`). Matching frozen-entry baseline/final groups are **4279.593/1324.731→1314.301/447.676** for Plotly and **682.377/197.107→395.479/116.361** for equations. Shared application/vendor chunks are outside those group totals; runtime navigation includes them. Mermaid's slight size growth and retained grammar support are explained in the results report.

Labelled final screenshots include `batch3-equation-dark-manual-zoom.png`, `diana-3d-after-theme.png`, `diana-3d-after-fit.png`, `batch3-mobile-375-Full-research-view-zoom200.png`, `batch3-mobile-390-Diagram-zoom200.png`, `batch3-large-trend-extrema-gaps.png`, `batch3-expected-experiment.png`, and `batch3-landing-{1440,390}-pipeline.png`. Actual exported chart/diagramSVGs, previews and contextual/full resultJSONs are listed in the handoff. Synthetic sources/provider results were not represented as live user data; import controls were never activated.

Final registry evidence is explicit in `final-registry-check.json/.log` and the executable `.cjs`, **exit0**: all seven actual settled Plotly `_fullData` trace types, real SVG paths or WebGL scene/canvas, no local status failures and zero page errors. Individually labelled screenshots are `batch3-supported-{scatter,bar,pie,heatmap,contour,surface,scatter3d}.png`. Earlier `batch3-renderer-cases.json` also retains the actual contour/scatter3d coordinates/types.

