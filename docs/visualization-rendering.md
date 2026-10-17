# Visualization rendering contract and budgets

The backend canonical contract is `backend/app/modules/research/visualization_contract.py`; generated frontend API types are in `frontend/src/types/api.d.ts`. Unsupported or unverifiable charts produce an explicit outcome without discarding an otherwise usable answer.

## Supported renderer set

`frontend/src/features/research/visualization-registry.ts` makes renderer-dimension decisions. The ordinary lazy bundle registers Plotly **4.0.0** core/scatter plus **bar, pie, heatmap and contour**. **scatter3d and surface** are registered only by the separate lazy 3D bundle. A requested 3D result is not substituted with a 2D chart. Unsupported trace names or unsafe/duplicate trace IDs fail before Plotly creates DOM selectors; the frame/table remain available.

The direct `plotly.js-dist-min` and `react-plotly.js` dependencies were removed after switching to lifecycle-managed `Plotly.react`. Core/trace source modules use the exact same Plotly version. Native rendering queues updates and catches asynchronous failures. A guarded ResizeObserver observes the local container; hidden/detached plots are ignored, synchronous and asynchronous resize failures are contained, and pending work is purged after unmount. Independent window resize handlers are not installed.

Every frame provides explicit series selection, exact selected-trace values, source/context access, JSON data export and contextual SVG export. Plotly's independent legend toggling and generic modebar are disabled. **Fit chart to data** explicitly fits selected values; **Reset chart view** restores supplied x/y ranges, or automatic bounds when none were supplied. Equation cards retain their own **Fit** action and internal widen-only range/reference state. View controls do not rewrite equation input state.

## Quantitative limits

Backend settings (environment variables; defaults):

| Setting | Default |
|---|---:|
| `VISUALIZATION_MAX_TRACES` | 32 |
| `VISUALIZATION_MAX_POINTS` | 10,000 per trace |
| `VISUALIZATION_MAX_TOTAL_POINTS` | 50,000 |
| `VISUALIZATION_MAX_GRID_CELLS` | 40,000 |
| `VISUALIZATION_MAX_LABEL_LENGTH` | 500 characters |
| `VISUALIZATION_MAX_BYTES` | 2,000,000 serialized bytes |
| `VISUALIZATION_MAX_DIAGRAM_CHARS` | 20,000 |

Analytics client preparation uses the same default trace/point/label/byte limits and at most 50 pie categories. Ordinal gap insertion is included before rendering budgets are accepted. A limit failure reports the reason and keeps the computed table; values/extrema/gaps are not silently dropped. Deployments changing server limits should align client preparation limits before raising accepted capacity; a more restrictive client can honestly withhold a larger server-accepted chart.

Within those accepted limits, **explicit validated numeric/date analytics trend lines with more than 1,000 points per trace** use a display-only target of at most 1,000 original points. Contiguous buckets retain their original min/max points; first/last points, global extrema, nonmissing segment endpoints and both endpoints of every null run are mandatory. Buckets never cross null gaps or metric/group boundaries. Output retains chronological index order, without averaging or fabricated coordinates. If mandatory gap preservation cannot fit the target, the full accepted trace remains and the reason is visible. Bars, pies, ordinal lines, trajectories and unspecified line order are never transformed. The metadata records each trace UID/name, original/display count, method/bucket width, retained ordered-point indexes and actual received-result row indexes (zero based). Plotted table/JSON/SVG describe the retained display; the separate analysis table and **Download full received result (JSON)** retain every received row. Rows omitted by server result/source caps remain explicitly unavailable rather than invented. Current backend analysis-plan result limits can keep ordinary new runs below this threshold; the policy also handles accepted larger saved/client results and synthetic regression fixtures.

Mermaid remains the full lazy package to retain all existing supported diagram grammars and their own lazy renderer chunks. The frontend bounds source to **20,000 characters**, **500 lines**, and **300 recognized relationship operators**; Mermaid itself receives `maxTextSize:20000` and `maxEdges:300`. Exceeding the budget preserves the raw relationship source and asks for simplification. These are complexity limits, not a claim that arbitrary syntax has identical cost. Mermaid retains `securityLevel:"strict"`; KaTeX non-trusting behavior is unchanged.

## Recovery, exports and equations

Local boundaries surround lazy chart/diagram/calculator components and canvas rendering. A failed lazy React component is recreated on its single explicit retry. Native Plotly and Mermaid asynchronous failures have a separate bounded retry. Persistent load/evaluation failures explain that reloading the page is the next action. Browser-network recovery still requires real-browser verification; mocked module recovery alone is not sufficient evidence.

An image export requires the rendered target's spec and revision to match current selected traces/layout, and checks that revision again after `toImage`. A changing/busy target reports an export failure instead of pairing an old image with new numerical metadata. JSON export remains available. SVG context is constructed with DOM `textContent`, includes title/origin/units/scope/source qualifiers, and embeds exact metadata. Selected provenance is remapped to exported trace indexes with original received indexes retained; experiment source arrays are interpretable through the same explicit trace mapping.

Equation evaluation uses `mathjs/number` with the existing arithmetic/function AST allowlist, finite-result/domain handling, and no `eval`/Function constructor. Curves, markers and references subscribe to the existing live theme hook without remounting the calculator. Illustrative inputs/sampled curves remain labelled, input values are preserved, and reference/widen-only axes are retained.

## Measurements

Current-source baseline and final bundle/runtime results are recorded in `docs/visualization-improvement-progress.md` and `docs/visualization-evidence-2026-10-15/`. Agreed budgets: ordinary 2D renderer **≤600 kB gzip** including its required stylesheet; equation module **≤150 kB gzip** subject to evaluator correctness. Raw/gzip sizes are loading evidence; runtime speed claims require the documented throttled-browser measurements. The retained full Mermaid dependency is intentional to avoid removing supported diagram types.
