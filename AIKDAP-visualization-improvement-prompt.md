# AIKDAP visualization improvement prompt

Improve AIKDAP's visualization system using the visualization audit completed 15 October 2026. Implement and verify the changes, including frontend/backend integration. Do not stop at recommendations or a cosmetic redesign.

## Scope and source material

Repository: `C:\Users\Divya Bangde\OneDrive\Desktop\My projects\AIKDAP`.

Audit: `C:\Users\Divya Bangde\.codex\attachments\b7b6501c-399a-4973-a537-0682c65e746d\Pasted text.txt`.

Supporting evidence: `docs/visualization-evidence-2026-10-14/` inside the repository. Read relevant logs, screenshots, and the archived synthetic fixture if available. The requirements below remain usable when the original attachment is unavailable.

ACTIVE_BATCH = ALL, executed sequentially: 1, then 2, then 3.

If I explicitly select a single batch, complete only that batch and its dependencies, and track the rest. Otherwise complete all three batches, with a tested and reviewed checkpoint after each. Preserve checkpoints and progress across context changes.

The goal is trustworthy, inspectable, accessible visualization: verify plotted values, preserve requested intent and every selected metric, validate structure, expose scope and partial data, improve interaction, then optimize loading.

Confirm each audit finding against current source before editing. Line numbers, saved runs, historical measurements, and passing tests are investigation leads, not proof of current behavior. Offline probes used model doubles; do not describe them as live model failures. Synthetic screenshots are renderer fixtures, not real user analytics results.

## Ownership and constraints

Read and follow applicable AGENTS.md instructions. Use the existing roster:

- Nicolas: backend provenance/value verification, chart/intent/status schemas, deterministic construction, API contracts, persistence, and backend regression tests. Do not edit UI.
- Newton: chart trace mapping and frame, client integration, paper-map controls, contrast, responsive layout, sliders, theme behavior, exports, renderer loading/recovery, and frontend tests. Do not write backend logic. Request missing endpoints/contracts from Nicolas; temporary development mocks must be identified and replaced before integration acceptance.
- Diana: reproduce and diagnose failing tests, renderer/runtime/build errors, environment issues, and dependency problems. Make targeted fixes; do not assign new product features to Diana.
- Reginald: review numerical fidelity, authorization, schema compatibility, accessibility, performance evidence, and regression coverage. Explain why each requested change matters; implementation owners resolve findings.

Where subagents are available, delegate independent work with explicit file ownership after agreeing on shared contracts. Otherwise perform the roles sequentially. Coordinate shared files and generated types; do not overwrite another agent's or the user's work.

Preserve the current React/FastAPI architecture, theme tokens, lazy loading, source/PDF passage viewing, evidence verdict text/icons, analytics tables/computation explanations, deterministic equation evaluation, illustrative-value notices, stable/widen-only equation axes, and reference curves. Retain Mermaid's restrictive security mode and KaTeX's non-trusting defaults. Keep changes focused on the audit scope.

Inspect existing APIs, persistence, tests, and dependency versions before choosing implementations. Use canonical shared contracts/generated client types where the project supports them. Plan compatibility for saved research runs and analytics results; missing legacy metadata must produce an honest unknown/unverified state, never fabricated provenance.

## Batch 1 — Trust and correctness

### V01: Verify the plotted data independently

Inspect `backend/app/agents/planner/synthesis.py` and the chart construction/acceptance path. Grounded prose or a valid citation ID must not automatically authorize chart values.

- Prefer deterministic construction from extracted tables or validated analytics outputs.
- Require point- or series-level provenance sufficient to resolve every plotted value, including the source asset/chunk or dataset/result version, units, and transformations. Validate source access and membership in the current authorized context.
- Verify values against actual evidence or deterministic results. For derived values retain input references and formulas, including explicit unit conversions and documented rounding/tolerance. Do not use a second unconstrained model assertion as the only verification.
- Distinguish measured, derived, illustrative, and user-entered origins. Source-grounded illustrative values remain illustrative.
- Withhold an unverifiable visual while preserving a usable answer; expose a clear visualization status/reason. Provide evidence access from the visual.

Acceptance: a passage stating 42 cannot authorize a plotted value of 999999. A supported conversion is traceable to its inputs/formula. A foreign-project citation cannot authorize a point. Unsupported chart values do not invalidate otherwise supported prose or silently remain visible.

### V02: Preserve all metrics and series

Fix `frontend/src/features/business-analytics/analysis-chart.ts` and dependent contracts rather than retaining only `chart.y[0]`.

Generate every intended `(series, metric)` trace with unambiguous names and stable identities. Validate requested fields against actual results. Use separate panels for incompatible units, or an explicitly supported axis policy; do not silently combine incompatible quantities or omit metrics.

Acceptance: two metrics across two regions produce four correctly mapped, labelled traces or two explicit metric panels. Cover grouped bars and lines, missing fields, null values, and incompatible units.

### V03: Typed, bounded visualization validation

Replace permissive chart dictionaries in `backend/app/modules/research/schemas.py` with discriminated schemas for the trace types actually supported by the renderer.

Validate required arrays, paired lengths, finite numbers, numeric/category/date compatibility, rectangular heatmap/surface grids, pie constraints, and 2D/3D consistency. Validate relevant layout fields through an allowlist; do not pass arbitrary generated configuration through as a complete rendering contract.

Define documented, configurable limits for trace count, points per trace/total, grid cells, label lengths, and serialized bytes. Enforce bounds before browser rendering and where practical before expensive parsing/allocation. Return actionable validation errors. Keep frontend/backend supported-type registries aligned.

Acceptance: reject metadata-only traces, scatter with x/y length mismatch, infinity/NaN, 2D scatter labelled chart3d, malformed grids, and oversized payloads. Valid bars, scatter, pies, heatmaps, and supported 3D surfaces remain usable. Add boundary cases at and above each budget.

### V04: Preserve requested intent and report completion separately

Represent requested type, axis semantics, series/metrics, and output status separately. Support `produced`, `not_enough_data`, `unsupported`, and `failed`, with an explanatory reason and appropriate next action. Persist/restore these fields consistently.

Normalize point lists using explicit intent; numeric years must not force a requested bar chart into a line. If intent is ambiguous, expose the interpretation instead of silently guessing. Add a bounded repair path for invalid/missing visual output that reuses existing valid results, respects run budgets, and does not rerun unrelated stages indefinitely.

Acceptance: a yearly bar request renders bars; a request with insufficient chart data displays an explicit explanation; text-only answer success is distinguishable from completion of a requested visual. Saved legacy runs remain readable with honest status.

### V05: Truthful pie-chart denominators

Either show the full distribution, calculate an accurate Other category from full-result aggregates, or prominently label the chart Share of displayed categories with a visible partial-data notice. Do not derive Other from omitted row count. Validate nonnegative finite values, a positive total, suitable aggregation, and bounded categories.

Acceptance: two of ten categories cannot appear to represent the complete distribution without explanation. Full/subset shares have distinct labels; all-zero/negative/invalid values fail clearly; any Other value reconciles with the full aggregate.

### V06: Explicit line ordering

For declared trends, sort within each series using validated numeric/date values. For ordinal categories use an explicit domain order, including calendar month order when declared. Preserve intentional path/trajectory sequence. Define null/gap, duplicate-x, and invalid-date policies; do not bridge gaps or aggregate duplicate points without a declared rule.

Acceptance: unsorted numeric/date trends become chronological; months do not sort alphabetically; trajectories retain intentional order; all metrics stay aligned after sorting.

### V10: Truthful graph empty states

Fix the `PaperMap.tsx` condition that treats fewer than two links as no citations. Distinguish no papers, zero citations, one valid citation, and multiple citations; explain isolated nodes separately. Render a single edge or a selectable relationship card.

Acceptance: zero/one/many-edge tests have truthful distinct output. A single real citation remains inspectable and selectable.

## Batch 2 — Interpretation and access

### V07: A shared, inspectable chart frame

Standardize a reusable frame with a meaningful title, brief supported finding, axis names/units where applicable, legend, data origin, evidence links, filters/time window, partial-data state, accessible data table, and deliberate export controls. Reuse analytics' existing table where practical. Never invent units, currency, findings, or source metadata.

Associate a short identification and detailed values/relationships alternative with charts and diagrams. Essential context must remain visible beside a visual and accompany exported/shared output. Label illustrative data at chart level, independently of overall grounding status. The table must describe exactly what is plotted, with access to full results when display aggregation/downsampling occurs.

Acceptance: users can interpret a chart without surrounding prose, inspect its values, and open supporting evidence. Exports retain title, units, source/scope, origin, and partial-data qualifications.

### V08: Equivalent keyboard access to paper-map actions

Add a synchronized searchable paper list/table with focusable selection controls opening the same details as canvas clicks. Support source opening and the existing import action through that accessible path. Preserve selection and predictable focus on panel open/close. Add labelled fit/reset/zoom and pause controls where applicable. Explain edge direction and node size; citation count must not imply question relevance.

Acceptance: keyboard users can find every paper, inspect details, open sources, and reach the import control without canvas pointing. Verify the actual production controls in a browser/accessibility tree. A mocked canvas replaced by HTML buttons does not establish production accessibility. Do not trigger imports against existing user projects during verification.

### V09: Contrast and non-color distinctions

Check current light/dark tokens against actual adjacent backgrounds. Adjust essential marks or outlines to meet applicable 3:1 non-text contrast. Use line styles, marker shapes, labels, or patterns so color is not the only series distinction. Keep identity stable across filtering/reordering; give many-series charts selection/aggregation instead of confusing palette repetition.

Acceptance: contrast calculations cover applicable backgrounds; series remain distinguishable in grayscale/common color-vision simulations; reordering preserves identity. Report checks performed without claiming whole-site accessibility certification.

### V11: Meaningful logarithmic controls

In `InteractiveEquationCard.tsx`, expose meaningful labels, units, and `aria-valuetext` based on physical values rather than transformed slider coordinates. Retain synchronized numeric input. Clearly identify logarithmic scales and any scale changes. Validate domains without disrupting reference curves or stable axes.

Acceptance: a 1 kg mass control announces Mass, 1 kilogram, rather than an internal log coordinate. Keyboard slider changes and numeric edits stay synchronized; invalid log inputs are handled clearly.

### V12: Readable mobile visual tasks

Offer diagrams a readable vertical layout or larger pan/zoom/detail view, plus a text relationship alternative. Avoid shrinking labels until illegible. For visually focused research requests, present a concise run title, expandable full question, essential qualifications, brief finding, and requested chart before lengthy explanation. Preserve evidence access and important uncertainty/origin notices.

Acceptance: verify at 1440x900 and 390x844, accounting for approximately 375 px usable width where scrollbars apply. Test touch, keyboard, long labels, and 200% zoom. After rendering/resize settles, no persistent page overflow occurs; necessary chart/diagram scrolling stays within labelled containers. Users can reach the requested visual without first reading a duplicated full-length prompt.

## Batch 3 — Consistency, recovery, and performance

### V13: Live equation theme updates

Use the existing theme subscription rather than memoizing accent color permanently. Update curves, reference traces, markers, labels, and backgrounds without remounting or losing input/axis state.

Acceptance: theme changes immediately update the real plot and preserve contrast, values, reference curves, and axis behavior.

### V14: Renderer recovery and purposeful controls

Add local error boundaries plus renderer-specific asynchronous error handling for Plotly, diagrams, and other affected visual surfaces. Preserve the answer and frame when rendering fails. Offer an actionable status, bounded retry, and view-data/source alternatives. Mermaid's raw-source fallback must explain the failure.

Choose modebar/export/fit/reset controls explicitly. Remove unexplained generic Share chart workflows; retain useful capabilities through equivalent controls. Add appropriate diagram download and data export with provenance. Treat untrusted labels/source safely and preserve existing renderer security restrictions.

Acceptance: injected load/render failures do not erase answers; retries are bounded; data remains inspectable. Real image/data/diagram exports are readable, contain required context, and work in both themes. Validate export failures and recovery, not just button presence.

### V15: Accurate explanatory pipeline

Compare `LandingStory.tsx` with actual ingestion/research orchestration. Either explicitly label the graphic as a simplified conceptual overview or show separate ingestion/query paths, optional best-effort understanding, and relevant fallbacks. Do not imply that understanding must finish before retrieval or that every run follows the same strict sequence. Preserve disclaimers on decorative sample quantities.

Acceptance: labels and relationships match current behavior; the illustration cannot reasonably be mistaken for live progress or measured production metrics.

### Rendering and loading performance

- Keep lazy loading. Register a smaller supported 2D Plotly trace set for ordinary charts and load 3D only when needed. Centralize supported-type decisions so an optimization never silently removes a supported visualization or changes intent.
- Inspect mathjs/Mermaid dependency contributions and narrow imports where supported and justified. Do not replace correct deterministic equation evaluation with unsafe evaluation.
- Enforce chart/diagram complexity budgets. For large series, apply explicit, appropriate aggregation/downsampling; label the transformation and preserve full data access. Do not misrepresent dropped extrema or gaps.
- Observe container resize for drawers, sidebars, tabs, and orientation changes. Judge lasting behavior after autosizing settles.
- Prefer 2D heatmaps/contours when they convey the requested information better, but make alternatives explicit rather than silently replacing a requested 3D deliverable.
- Measure production bundle raw/gzip sizes before/after, initial renderer load, parsing, and interaction on a documented slower-device or browser-throttling profile. Build time and chunk size alone do not establish runtime speed.

Audit baselines were Plotly 4,279.53/1,324.70 kB, equation 682.61/197.29 kB, and Mermaid 659.95/159.90 kB (minified/gzip). Re-measure current source before comparing. Agree on concrete budgets from current measurements and supported feature needs; explain retained large dependencies instead of inventing improvement percentages.

## Validation, progress, and handoff

First inspect repository state and preserve user changes. Maintain `docs/visualization-improvement-progress.md` mapping V01–V15 and performance items to owner, status, changed files, acceptance evidence, and remaining limitations. Include necessary schema/persistence migrations, compatibility handling, generated API types, and configuration documentation.

Add focused regressions for the adversarial 42/999999 case; transformations/provenance/foreign-project references; two metrics x two groups and incompatible units; numeric-year bars; missing visual output; partial/invalid pies; trend versus trajectory ordering; invalid numbers/arrays/grids and payload limits; zero/one/many citation edges; production keyboard controls; physical slider announcements; live theme changes; narrow layouts; renderer failures; and contextual exports.

Use isolated backend/unit tests for deterministic logic and real-browser checks for Plotly, canvas, Mermaid, accessibility controls, theme changes, resizing, and exports. Derive commands from repository scripts. Existing 55 frontend and 35 backend passing audit tests are historical context, not completion criteria. Tests must exit cleanly without uncaught asynchronous errors.

Use synthetic/local fixtures and disposable data for verification; do not modify saved user projects or invoke fixture import actions. Label screenshots and measurements with data origin, viewport, theme, and environment. If services or live providers are unavailable, finish independent work and report those checks as unverified. Do not claim that component mocks establish graphical fidelity, screen-reader usability, or live-model accuracy.

Have Reginald review each batch before moving on. Resolve material findings and rerun affected checks. Do not stop after planning or seek routine confirmation for reversible local work. Keep production deployment outside this implementation task.

Final delivery must state implemented behavior, changed files, contract/migration notes, exact test commands and exit codes, labelled screenshots/export artifacts, before/after performance evidence, review resolutions, and any remaining blockers. Mark work complete only when its acceptance criteria are verified.

Start with Batch 1 now and continue through the selected scope.
