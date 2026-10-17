# Visualization implementation results

Three sequential batch checkpoints and all V01–V15 details are tracked in [visualization-improvement-progress.md](visualization-improvement-progress.md). Existing user changes were preserved; the initial working tree inventory and local patch backup are retained with the evidence. No production deployment or user-database migration was performed.

**All three batches approved; no material blocker remains.** Reginald's final17October review is recorded in [visualization-reginald-batch3.md](visualization-reginald-batch3.md). Scientific-value, real3D interaction and large-series findings were resolved and reverified before approval.

## Resulting behavior and principal files

- **Numerical trust and contracts:** `backend/app/agents/planner/visualization.py` independently verifies every plotted observation against authorized evidence, retains input references and declared transformations, rejects category/value swaps, and keeps illustrative data illustrative. Raw source tokens use exact Decimal checks; derived arithmetic uses exact rational inputs, with at most one binary64 ULP for ordinary unrounded noninteger results. Underflow, unsafe integers and extreme exponents fail safely. `backend/app/modules/research/visualization_contract.py` defines bounded typed trace/layout contracts. `visualization_outcome` separates requested intent and visual completion from answer completion.
- **Analytics and experiments:** `analytics/executor.py` preserves scalar precision and exact unsafe integers in tables. `analysis-chart.ts` preserves every metric×series, declared ordering and null gaps, labels subset pies, and separates unknown/incompatible units. For explicit numeric/date trends above1000 points, `trend-reduction.ts` selects original bucket minima/maxima, endpoints, global extrema and null-gap boundaries; complicated gaps retain the full accepted trace with an explanation. Transformation counts, method and original-row mapping accompany the plot; the full received table and separate JSON download remain exact. Bars, pies, ordinal lines and trajectories are never sampled. `experiment_visualization.py` validates before allocating; `ExperimentVisualizationChart.tsx` explicitly identifies expected test-case values, plan/version, selected fields, case IDs and available source references.
- **Inspectability and access:** `ChartFrame.tsx` provides title/finding/origin/scope/units, source actions, series selection, exact plotted values and contextual JSON/SVG downloads. `PaperMap.tsx` provides searchable keyboard controls and honest zero/one/many states. Theme tokens, stable patterns/dashes/shapes, physical-value logarithmic controls and narrow-view scroll regions address V08–V12.
- **Rendering and recovery:** `PlotlyVisualization.tsx`, `plotly-view.ts`, `RendererBoundary.tsx`, `MermaidDiagram.tsx` and `visualization-export.ts` contain local async/load error handling, one explicit retry, guarded container resizing, view persistence, explicit Fit/Reset, and settled-image export guards. 3D controls send only scene-axis updates. Theme changes preserve public camera/range state when the visual meaning is unchanged.
- **Accurate explanation:** `LandingStory.tsx`, `PipelineRail.tsx` and `ProviderRouting.tsx` distinguish conceptual ingestion/query paths, optional background understanding, configured provider examples, reranking/fallback conditions, and citation identity versus claim/value verification.

## Contract and deployment notes

Migration **028_visualization_outcome.py** adds nullable JSONB metadata. Legacy rows remain intact; missing independent verification produces an honest unknown/unverified state and withholds unsupported numeric plots while retaining prose/evidence. The disposable PostgreSQL migration check verified upgrade, legacy NULLs, JSON round-trip, downgrade and re-upgrade, then removed its container. Apply migration028 through the normal deployment process before using the new persistence field.

`frontend/src/types/api.d.ts` was regenerated from canonical OpenAPI. There is no fabricated provenance for legacy rows or expected experiment values. Quantitative settings and supported traces are documented in [visualization-rendering.md](visualization-rendering.md). Full Mermaid remains lazy to preserve existing diagram grammars and restrictive security settings; mathjs uses its number entry point and retains the arithmetic AST allowlist.

## Verified browser evidence

All fixtures are clearly labelled synthetic, use production components, and make no live project writes or provider-accuracy claims. Chromium **153.0.8010.12** was used. Ordinary checks use the installed headless Chromium; the explicit 3D diagnostic uses software WebGL flags `--use-angle=swiftshader --enable-unsafe-swiftshader` with1440×1100 viewport.

| Evidence / executable reproduction | Verified behavior |
|---|---|
| `final-contract-browser.cjs`, `.json`, `.log` | Four metric×series traces, January→February→March/null gaps, numeric-year bars, subset pie label, exact9007199254740993 table, selected11-trace JSON, expected-only experiment case/plan/source metadata, source drawers, paper-map keyboard focus restoration; exit0 |
| `final-browser-check.cjs`, `.json`, `.log` | Equation input2kg + manual zoom + reference/current curves survive theme switch; actual palette changes; container-only width1054→602; zoomed React navigation has no detached renderer errors; touch375/390 at200% reflow has no page overflow; four actual SVG downloads open as validXML and render; exit0 |
| `diana-3d-diagnostic.cjs`, `diana-3d-controls.json` | Real mouse orbit changes camera, theme preserves it, Fit/Reset autorange all scene axes and restore default camera without caught view errors; exit0 |
| `final-recovery-check.cjs`, `.json`, `.log` | Aborted engine, outer lazy chart and project PaperMap chunks preserve local alternatives/project shell; one retry then explicit reload guidance; unblock+reload recovers; exit0 |
| `final-landing-check.cjs`, `.json`, `.log` | Actual production homepage conceptual paths/optional work at1440/390px; zero page errors or overflow; exit0 |
| `final-large-trend-check.cjs`, `.json`, `.log` | Actual5000→912-point display reduction retains−999/+1234 extrema, gap2000–2005 boundaries and neighbors, endpoints and order. Actual plotted/full downloads reconcile every retained point to its original row; full5000 rows preserved; exit0 |
| `final-registry-check.cjs`, `.json`, `.log` | All seven actual settled trace types: scatter,bar,pie,heatmap,contour,surface,scatter3d. Real SVG paths or WebGL scene/canvas, no local errors or unexpected page errors; exit0 |
| `batch3-diagram-recovery.json` | Invalid Mermaid fails locally, retries once, keeps relationship source; no leaked renderer error SVG/page overflow |

Recovery resource errors are intentionally injected and recorded separately from unexpected page errors. Source-navigation responses for synthetic DOI/asset fixtures are locally intercepted; these checks establish navigation behavior, not external source availability. Import actions are reachable but were never activated.

Labelled screenshots and exports in `visualization-evidence-2026-10-15/`:

- `batch3-equation-dark-manual-zoom.png`:2kg equation with preserved manual zoom in dark theme.
- `diana-3d-after-theme.png`: actual rotated surface after theme change; `diana-3d-after-fit.png`: working Fit state.
- `batch3-mobile-375-Full-research-view-zoom200.png` and `batch3-mobile-390-Diagram-zoom200.png`: actual touch viewport/reflow checks.
- `batch3-landing-1440-pipeline.png` and `batch3-landing-390-pipeline.png`: production conceptual ingestion/query explanation.
- `batch3-large-trend-extrema-gaps.png`, `batch3-large-trend-plotted.json`, `batch3-large-trend-full-result.json`: labelled reduction with exact full-data access.
- `batch3-supported-{scatter,bar,pie,heatmap,contour,surface,scatter3d}.png`: individually labelled actual supported renderers.
- `batch3-verified-chart-{light,dark}.svg`, `batch3-diagram-{light,dark}.svg` and corresponding `-export.png`: actual downloads with visible context and embedded exact metadata.
- `batch3-verified-chart-context.json`, `batch3-selected-series-context.json`, `batch3-expected-experiment-context.json`: actual contextual data downloads.
- `batch2-contrast.json`, grayscale/deuteranopia/protanopia screenshots: measured token contrast and limited appearance simulations. Minimum essential mark contrast4.506:1 light /3.813:1 dark on tested adjacent backgrounds; these checks are not whole-site accessibility certification.

## Tests, builds and performance

Run from the repository root unless a working directory is specified:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_visualization_contract.py backend/tests/test_grounded_synthesis.py backend/tests/test_research_web_and_visuals.py backend/tests/test_analytics_executor.py backend/tests/test_analytics_plan.py backend/tests/test_analytics_graph.py backend/tests/test_analytics_grounding.py backend/tests/test_analytics_dataset.py backend/tests/test_claim_wiring.py backend/tests/test_experiment_visualization_bounds.py -q --tb=short
# 259 passed, 1 existing LangGraph pending-deprecation warning; 6.51s; exit0.
# Log: visualization-evidence-2026-10-15/final-backend-tests.log

# Working directory: frontend
npm test -- --maxWorkers=2
# 65 files /405 tests passed; 62.63s; exit0; no warnings.
# Log: visualization-evidence-2026-10-15/final-frontend-tests.log
npm run build
# tsc-b + Vite; exit0; Vite26.37s.
# Log: visualization-evidence-2026-10-15/final-application-build.log

# Working directory: backend; prior disposable PostgreSQL/Redis integration checkpoint
.venv/Scripts/python.exe scripts/check.py integration tests/test_experiment_visualization.py tests/test_experiment_visualization_bounds.py tests/test_analytics_executor.py tests/test_analytics_graph.py
# 95 passed, no skips; 3.87s; exit0. Final research verifier-only changes are
# covered by the259-test suite above; no later DB/schema change occurred.
.venv/Scripts/python.exe scripts/verify_visualization_migration.py
# exit0; disposable migration upgrade/downgrade/re-upgrade/JSON round-trip.
```

Independent final reviews passed backend259tests, frontend3D8tests and reduction31tests; see role-specific review notes for exact affected-file commands and independent durations. The405-test consolidated run includes the final scene controls, public view retention and trend reduction regressions. Every executable browser check in the evidence table is run with `node docs/visualization-evidence-2026-10-15/<name>.cjs` from the repository root; each final log records exit0 through the associated tool result. Static localhost server reproduction is `node docs/visualization-evidence-2026-10-15/serve-fixture.cjs <dist-or-evidence-directory> <port>`.

Historical failed checks were diagnosed rather than discarded: the AppShell full-suite failure came from an unmocked fake-token request into a live backend; Diana corrected only that test fixture. The real 3D browser failures led to scene-only controls and public view retention; both were verified after rebuilding. The old tolerance-based verifier was replaced after adversarial review. Earlier failed diagnostic logs are not final passing results.

### Final matched performance — 17 October 2026

Hardware: Intel Core i7-13650HX,20 logical CPUs,16GB RAM. Chromium153.0.8010.12;1440×900,dpr1;4× CPU slowdown;10Mbps down/5Mbps up;40ms network latency; gzip transport; cache disabled. Three fresh-context baseline/final pairs were interleaved in one session with builds/tests finished. The archived baseline and frozen comparison fixture select the unchanged simple research-plot path before analytics mounts. The baseline contains an early analytics mapping revision; no timing claim depends on that path. Equation loading is additional module load/render after Plotly is warm, not a cold whole-page calculator launch. Keyboard timing is one ArrowRight action plus two animation frames, not a separate settled-export measurement.

Reproduction: `node docs/visualization-evidence-2026-10-15/final-performance.cjs` — **exit0**, six untraced runtime samples, all zero page errors, plus two separate compile-trace runs. See `benchmark.cjs`, `matched-runtime-2026-10-17.json`, `matched-runtime-summary.json`, `final-baseline-compile.json` and `final-optimized-compile.json`.

| Runtime measure | Baseline median (range), ms | Final median (range), ms |
|---|---:|---:|
| Initial research Plotly renderer |8322 (8216–8556)|1709 (1700–1848)|
| Additional equation module/render |1952 (1869–2062)|1038 (1018–1068)|
| Equation keyboard action +2frames |288 (212–380)|139 (132–173)|
| Mermaid cold module/render |647 (541–1785)|602 (579–645)|
| Initial CDP ScriptDuration |6016.706 (5871.847–6136.765)|313.188 (301.980–367.631)|
| Full benchmark CDP ScriptDuration |7268.064 (7237.980–7462.384)|974.896 (896.860–1005.708)|
| Full benchmark CDP TaskDuration |9301.365 (9087.183–9503.526)|2034.498 (1963.581–2161.789)|

Observed initial-render median reduction: **79.5%**. This is a three-sample synthetic comparison, not a statistical or production-wide guarantee. Mermaid ranges overlap and include a slow baseline outlier; no dependable Mermaid runtime improvement is claimed. Earlier-session measurements remain historical checkpoints; these matched final samples supersede their headline timings.

Separate traced initial-render runs: V8.CompileCode inclusive duration **2010.586→329.910ms**, V8.ParseProgram **844.146→20.444ms**. Trace-event categories nest/overlap and must not be summed or substituted for untraced wall time.

Bundle sizes are exact Node-gzip bytes converted to decimal kB. Reproduction: `node docs/visualization-evidence-2026-10-15/measure-bundles.cjs <dist> <output.json>`. `baseline-bundles.json` and `final-performance-bundles.json` compare the same frozen entry; `final-application-bundles.json` records the actual application build separately.

| Renderer chunks | Baseline raw/gzip kB | Frozen final raw/gzip kB | Actual final application raw/gzip kB |
|---|---:|---:|---:|
| Plotly renderer, including required CSS and wrapper |4279.593 /1324.731|1314.301 /447.676|1314.534 /447.794|
| Additional lazy3D engine |included in old full bundle|570.331 /171.591|570.331 /171.591|
| Equation module |682.377 /197.107|395.479 /116.361|396.107 /116.618|
| Mermaid module |659.096 /159.487|661.395 /160.451|662.260 /160.837|

Ordinary2D and equation budgets (**≤600kB/≤150kB gzip**) pass. Shared application/vendor chunks are outside these renderer-group totals; actual navigation timings include them. Mermaid grew slightly because recovery/context logic was added; retaining its full lazy grammar support was intentional. Build times are not runtime gains.

Final isolated fixture commands from `frontend` (owned disposable directories were resolved inside this repository before emptying):

```powershell
$env:VISUALIZATION_OUT_DIR='../docs/visualization-evidence-2026-10-15/final-qa-dist'
npx vite build --config visualization-check.config.ts --emptyOutDir
# exit0;25.05s; final-qa-build.log
$env:VISUALIZATION_OUT_DIR='../docs/visualization-evidence-2026-10-15/final-performance-dist'
$env:VISUALIZATION_ENTRY='visualization-performance.html'
npx vite build --config visualization-check.config.ts --emptyOutDir
# exit0;23.77s; final-performance-build.log
```

Disposable comparison servers: baseline-dist4174, final-qa-dist4176, final-performance-dist4177, frontend/dist4179, evidence SVGs4180. The homepage test requests index.html and sets the browser history to `/` before application startup because the minimal static fixture server has no SPA route fallback. QA HTML/entries are excluded from the normal application build. Dist outputs are ignored local evidence; rebuild them from the retained fixtures/configuration when needed.

## Practical limits

No live model/provider accuracy or GPU performance claim is made. Performance is a small synthetic renderer comparison on one machine, under a documented throttle, not a production SLA. Hash-based series encodings can collide; series selection remains available. Anonymous legacy traces lack stable source identities and fall back to position. Over-budget unsupported/unsafe charts are withheld with full available table/source alternatives rather than silently altering chart intent. Changing server capacity settings requires aligning client preparation limits. Mermaid complexity limits bound source size/operators but do not prove identical cost for every grammar.
