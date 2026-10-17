# Diana: final regression diagnosis

Scope: runtime/environment and test-fixture diagnosis only. No visualization renderer, backend, dependency, or product behavior was changed.

## Finding

The first final frontend run failed only `AppShell > changes the default persona from the sidebar`: **62 files passed, 1 failed; 393 tests passed, 1 failed**, duration **78.99 s** (`final-frontend-tests.log`). The failing expression directly indexed `authService.updateMe.mock.calls[0][0]` before a call existed. A normal isolated run passed all six shell tests, demonstrating timing dependence rather than a consistently failing chart integration.

Checking the current code showed that `AppShell` mounts the real `ActiveWorkToast`, whose projects, research, and assets queries were not mocked by `AppShell.test.tsx`. They therefore used the configured local API with the test's fake `tok` / `ref` credentials. The shared client clears the auth store after rejected credentials. A shuffled reproduction showed the profile and persona selector disappearing during the assertion (expanded DOM evidence in `appshell-expanded2-diana.log`). The diagnosis is environmental coupling through these background requests. Awaiting the mutation alone did not resolve it; the interim full run still failed **1/394 tests** in **68.40 s**.

The auth/session production files were already modified in the user's initial checkout, as recorded in `initial-working-tree.txt`. `AppShell.tsx` has no visualization-task diff. These files were preserved.

## Targeted repair

Only `frontend/src/layouts/AppShell.test.tsx` was changed:

- Mock the three background service modules and resolve their list queries to empty lists in `beforeEach`, retaining the real toast component without contacting the running backend.
- Explicitly unmount before resetting the auth store, preventing mounted observers from reacting to teardown cache invalidation.
- Await the mutation contract and then the current rendered selector, avoiding reliance on native selection timing or a detached element.

No assertions were removed or skipped. No timeouts were increased.

## Verification

Commands run from `frontend/`:

| Command | Exit | Result | Evidence |
| --- | --- | --- | --- |
| `npx vitest run src/layouts/AppShell.test.tsx --maxWorkers=1 --reporter=verbose` (before fix) | 0 | 6/6; 3.41 s; React act warnings | Tool result; isolation established intermittency |
| `npx vitest run src/layouts/AppShell.test.tsx --maxWorkers=1 --sequence.shuffle --sequence.seed=15317` (await-only interim fix) | 1 | 5/6; 3.58 s | `appshell-shuffled-diana.log` |
| Same seeded isolated command after service-fixture repair | 0 | 6/6; 2.78 s; no act warnings | `visualization-evidence-2026-10-15/appshell-isolated-fixed-diana.log` |
| `npm test -- --maxWorkers=2` after service-fixture repair | 0 | 64/64 files; 396/396 tests; 103.33 s; no warning output | `final-frontend-tests-diana-fixed.log` |

The final suite contains one additional file and two additional tests supplied by the concurrent visualization work. Its wall time is a verification duration, not a chart-performance comparison; concurrent browser/build activity and the differing suite size prevent using it as a performance benchmark.

All logs mentioned above are in `docs/visualization-evidence-2026-10-15/`. The directory retains its original evidence date; this follow-up was performed on 17 October 2026 in the user's timezone.

The suite uses jsdom; real-browser graphics verification remains with the root/Newton/Reginald work. This diagnosis does not establish production API availability or authenticate fake test credentials.

## Independent 3D browser diagnosis

The parent reported a rendered 3D surface whose camera did not change after an automated drag. A separate Playwright process using Chromium headless shell revision 1243 (Chromium 153) verified working mouse orbit without changing application source.

Reproduce with `node docs/visualization-evidence-2026-10-15/diana-3d-diagnostic.cjs` from the repository root; exit **0**. The script loads the audit surface at `http://127.0.0.1:4176/visualization-audit.html?case=3D%20surface`. Launch flags are `--use-angle=swiftshader --enable-unsafe-swiftshader`; viewport is **1440 × 1100**. It waits for `data-chart-rendering="false"` and then 1500 ms, targets `.gl-container canvas`, and verifies `document.elementFromPoint` hits that canvas. Its visible box was **x=265, y=577, width=881, height=284**. A left-button drag from its center **(705.5, 719)** to **(881.7, 775.8)**, in 20 steps with 150 ms pauses after press/before release, changed `getCamera().eye` from **(1.25, 1.25, 1.25)** to approximately **(0, 0, 2.165063509461097)**. Native pointer/mouse events reached the canvas and `staticPlot` was false; scene drag mode was `turntable`.

Evidence: `diana-3d-diagnostic.log` and visually inspected `diana-3d-diagnostic.png` in the same evidence directory. No browser page errors or console errors were reported. This establishes that the current surface supports real browser orbit with software WebGL. The original no-op drag was not independently reproduced; likely differences to inspect are viewport clipping, target hit testing, or render timing. No source or dependency fix was required.

### Extended interaction checks: failures handed to Newton

The diagnostic was then extended to verify theme persistence and the actual Fit/Reset buttons. This extended run exits **1** and saves failure evidence; it supersedes the initial orbit-only successful run for the complete interaction check.

- **Theme persistence fails:** the dragged camera eye is approximately `(0, 0, 2.165063509461097)`; after clicking **Dark theme** and waiting for the next completed chart-render revision, it becomes `(1.25, 1.25, 1.25)`. `themeCameraPreserved` is false.
- **Fit fails:** clicking **Fit chart to data** produces the visible status `The chart view could not be updated. Retry the renderer or inspect the plotted data.` and disables **Reset chart view**, preventing the requested reset verification.
- Importing the runtime from its actually loaded browser asset and calling `relayout` with the same mixed 2D/scene payload reproduces `TypeError: Cannot read properties of undefined (reading '_inputDomain')`. Removing the 2D `xaxis`/`yaxis` updates while retaining all three scene axis autoranges and the default camera succeeds (`sceneOnly.success: true`). This isolates the Fit error to the update payload, rather than software WebGL.

Exact browser version **153.0.8010.12**; reproduction command unchanged. The JSON snapshot, including camera/axis/status states and runtime rejection stack, is `diana-3d-controls.json`. Screenshots are `diana-3d-after-theme.png` and `diana-3d-after-fit.png`. The current diagnostic log retains the failing run. Newton owns production corrections; root and Reginald were informed. Diana made no product edits. Both theme persistence and the full Fit/Reset sequence require fresh real-browser validation after the corrected build.
