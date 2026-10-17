# Phase 1 independent review — Reginald

Review date: 14 October 2026. Scope: saved Phase 1 backend/frontend changes, migrations 026/027, regression tests, and isolated verification tooling. Applicable instructions: root `AGENTS.md`, `AIKDAP-improvement-prompt.md`, and the original 13 October report. This is an independent source review and bounded deterministic test review; it is not production or live-provider certification.

## Findings and resolution

| Finding | Impact and required correction | Owner / current disposition |
| --- | --- | --- |
| P1: missing primary values could be marked RESOLVED by attaching an unrelated primary quote | Quote membership alone does not show that the missing primary parameter exists. Preserve the map stage's missing-information verdict and reject unsupported resolution; add a valid-unrelated-primary-quote regression. | Resolved by Nicolas/coordinator: conservative missing-information guard and valid-unrelated-primary-quote regression; independently rerun successfully. |
| P2: grounded history sent `status=grounded` to an execution-status enum | Dashboard's Grounded Answers destination returned HTTP 422. Use an explicit grounding filter with the same completed/nonempty-answer definition as totals; bind cursors to that filter and cover API/client behavior. | Resolved by Nicolas/coordinator/Newton: `grounding_status` API/client filter requires completed runs and nonempty trimmed answers, with matching cursor scope and >100-record API pagination regression. |
| P2: rebuild UI omitted backend `queued` status | A queued response stopped polling immediately and left Resume enabled, making normal queued work appear idle. Include queued in polling/working states and exercise queued-to-completed transitions. | Resolved by Newton: queued work polls and disables resume; queued-to-ready and retry transition regression saved and passing in the coordinator's targeted frontend run. |
| P2: SSE callbacks lacked session guards | Closing streams and clearing caches does not independently guard a late callback that calls `setQueryData`. Capture the session epoch, reject late step/end/error events, scope step keys by session, and test A stream callback after B login. | Resolved by Newton: session-scoped step keys, cancellation and epoch fences on token/step/end/error callbacks, and late A callback regression saved and passing in the coordinator's targeted frontend run. |
| P2: blanket integration classification skipped pure embedding regressions | Importing `async_session_factory` marked every test in the module integration, including full-space identity and vector validation unit cases. Classify service-dependent cases explicitly or separate pure cases so offline runs exercise them. | Resolved by Diana/coordinator: pure identity/task/vector cases moved into `test_embedding_spaces.py`; all eight current cases, including empty optional task configuration, run offline and passed independently. |
| P2: mismatched worker could regress completed or actively claimed rebuild status | The failed-claim configuration-mismatch branch wrote `failed` before checking completed/busy state. A stale worker could overwrite a terminal result or another worker's valid claim. | Resolved by coordinator: completed guard precedes mismatch processing; SQL mismatch updates require nonterminal state and absent/expired lease. Added real PostgreSQL regression covers both completed and live-claim cases. Coordinator's affected disposable suite: 41 passed, exit 0, 5.83s (`backend-phase1-review-final.log`). |

## Confirmed design properties

- Comparison sources are all authorized before passage loading/model calls. Cache inputs include real source IDs/checksum/version/chunk content, goal/context, extractor/model/prompt identity. Persisted artifacts retain provenance and restore reauthorizes every source and marks changed versions stale.
- Comparison validators bind cited chunks and quotes to actual paper passages, replace model-supplied titles/locations with source metadata, and require quoted primary/supporting claims. No fabricated supporting metadata or random replacement primary IDs remain in the reviewed path.
- Embedding identities distinguish equal-width spaces by provider/model/version/tasks. Legacy vectors retain NULL identity. Retrieval requires both compatible chunk identity and compatible activated project identity.
- Rebuilds stage vectors durably, hash current content, retain successful batches across retries, use renewable token claims, and validate the complete current corpus inside activation. Project locking coordinates chunk replacement with activation. Model calls occur outside rebuild transactions. A configuration switch fails closed until its configured space is activated.
- Aggregate totals are SQL counts over owned records, with explicit document/grounded definitions. List cursors use descending timestamp/UUID ordering and bind owner/project/filter scope. Existing array routes remain compatible; new page routes expose compact summaries.
- Session invalidation centralizes cancellation/cache clearing, remounts routes at session boundaries, and checks epochs after JSON/blob parsing and token refresh. SSE keys and all callbacks are session-fenced. Multi-page collection also checks its originating epoch before/after each page; persona updates use the session-scoped profile cache key.
- Dispatch failure bookkeeping conditionally changes only queued, unclaimed rebuilds; a publish-then-error cannot overwrite a worker's claimed/result state. Readiness refreshes existing ORM project/job instances so a concurrent worker activation is not hidden by the request's identity map.
- Ruff changes are focused formatting edits. Test environment changes isolate settings/provider health and deny unintended network calls; disposable integration tooling has bounded service startup/checks and cleanup.

## Independent check evidence

Commands run from `backend/`, using the repository virtual environment:

| Command | Exit code | Exact result |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/test_cross_paper_real.py -q --timeout=30` | 4 | pytest-timeout is unavailable; `--timeout=30` is unrecognized. No tests executed. |
| `.venv/Scripts/python.exe -m pytest tests/test_cross_paper_real.py -q` | 0 | 22 passed, 1 warning in 1.33s. |
| `.venv/Scripts/python.exe -m pytest tests/test_cross_paper_real.py tests/test_cross_paper.py tests/test_phase1_embeddings.py -q` | 0 | 22 passed, 11 skipped, 1 warning in 2.83s. Embedding module was skipped by blanket integration classification; this exposed the finding above. |
| `.venv/Scripts/python.exe -m pytest tests/test_cross_paper_real.py tests/test_embedding_spaces.py -q -m 'not integration'` | 0 | 29 passed, 1 warning in 1.73s. All seven pure embedding regressions execute without services. |
| `.venv/Scripts/python.exe -m pytest tests/test_cross_paper_real.py tests/test_embedding_spaces.py -q -m 'not integration'` (after empty-task normalization regression) | 0 | 30 passed, 1 warning in 1.60s. All eight current pure embedding cases execute without services. |

The warning is LangChainPendingDeprecationWarning about `allowed_objects`. Providers in these independent checks are deterministic doubles. Coordinator/Diana evidence is recorded separately in `docs/improvement-progress.md`: disposable PostgreSQL/Redis integration, seeded migration roundtrip, complete frontend tests/build, unit suite, and broader integration checks. Those results are not represented as independently rerun by Reginald.

Live model contradiction/entailment quality, OCR and full browser citation interaction remain separately bounded checks. The broad integration execution failures concern unavailable approved dispatcher image digests; a separately built source dispatcher does not certify the approved image or justify relaxing launcher policy. Real broker/worker diagnostics are recorded separately when performed. No production deployment or corpus-scale performance certification is claimed.

## Review disposition

All six material source findings have owner corrections and meaningful regression coverage. No unresolved material source finding remains in the reviewed Phase 1 paths. The affected real PostgreSQL suite passed after the final concurrency correction. The coordinator also reports full frontend tests (341 passed across 54 files, exit 0, 16.88s) and production build (exit 0, 44.77s); exact commands and all broader evidence belong in the progress record. Reviewed implementation is acceptable subject to the documented external acceptance limits: passing deterministic/provider-double tests does not certify live-model quality or an unavailable approved execution image. Phase 2–5 requirements remain backlog. Corpus-size rebuild performance and per-project summary request fan-out deserve measured Phase 3 evaluation; no performance gain is claimed from this review.
