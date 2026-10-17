# Improvement progress — 14 October 2026

ACTIVE_PHASE = 1. Phase 1 implementation delivered; local regression and migration gates pass. Full phase acceptance remains conditional on the execution-image and live-service/browser gates below. No production deployment performed. Later phases remain backlog.

The prompt and existing review/evidence documents were untracked user files before this run and are preserved. Findings below were checked against current source. Agents interrupted by account usage limits were resumed on the user's `continue` instruction.

| Item | Owner | Dependencies | Status and acceptance evidence | Blockers |
| --- | --- | --- | --- | --- |
| 1A Real cross-paper analysis | Nicolas / Newton; coordinator completed interrupted work; Reginald review | Authorized assets/chunks, versioned understanding, migration 026 | Implemented and locally verified. All sources authorized before extraction; real asset/chunk IDs and exact quotes validated; versioned understanding and immutable comparison artifacts persist. Conflicting passage fixtures, foreign-source rejection, cache invalidation, new-session restore, source selection/open target, stale citation blocking, insufficiency and missing-primary-value regressions pass. | Live model quality and full PDF browser journey unverified |
| 1B Embedding compatibility/rebuild | Nicolas role (coordinator) / Newton; Reginald review | Migration 027 after 026; pgvector, worker/provider | Implemented and locally verified against real pgvector and a real Redis/Celery worker. Full identity, activated project space, conservative legacy NULLs, validated staged batches, lease/token claims, retry checkpoints and atomic activation. Tests cover equal-width incompatible spaces, successful-batch reuse, expired leases, duplicate claims, invalid vectors, dispatch recovery, mismatched workers and coverage. UI queued polling verified. Real worker resumes successful batch checkpoints and reaches 65/65 coverage. | Live provider and hard worker-kill/redelivery timing unverified |
| 1C Counts/pagination | Nicolas / Newton / coordinator | Scope-bound cursor API; aggregate SQL | Implemented and locally verified. SQL totals independent of page length; owner/project/filter-scoped timestamp+UUID cursors and compact list responses. Real database test has 107 projects/assets/runs at tied timestamps and 103 grounded answers; frontend accesses item 101, retains earlier pages and retries failed subsequent pages. Grounded history filter agrees with totals. | No phase-specific local blocker |
| 1D Session invalidation | Newton; coordinator completed SSE/persona follow-up | Shared invalidation and cancellation, session epoch | Implemented and locally verified. Logout, rejected refresh and rejected replacement token share request cancellation/cache clear; session keys and epoch checks fence profile, token refresh, JSON/blob/form and multi-page results. SSE callbacks are fenced. A→refresh failure→B and late A request/refresh/blob/SSE regressions pass; scoped persona update preserved. | No phase-specific local blocker |
| 1E Clean baseline | Diana; coordinator final checks | Disposable PostgreSQL/Redis; isolated settings/providers | Completed frontend/build and isolated unit gate. Promise mocks repaired; deliberate jsdom canvas/scroll/navigation stubs; mutable settings/provider-health reset; imported LiteLLM catalogue/network isolated; disposable DB fixtures bounded. Confirmed 17 E701 fixed. 341 frontend and 830 isolated backend tests pass; relevant integration journeys pass. | Broad integration still has 9 approved-image failures; OCR/live checks explicitly skipped |

Test output is saved under `docs/improvement-evidence-2026-10-13/`; `*.log` files are ignored by Git, while the exported OpenAPI JSON is retained there. Exact results appear below. Mocked providers establish deterministic correctness, not live-model quality. Subagent capacity interruptions were completed sequentially by the coordinator within the assigned roles, followed by an independent Reginald review.

## Phase 2 backlog (not implemented)

| Item | Owner | Dependencies | Status |
| --- | --- | --- | --- |
| 2A S3-compatible shared durable storage, authorized reads, cleanup/migration | Nicolas / Diana | Storage configuration and real integration environment | Backlog |
| 2B Transactional outbox, idempotent claims, leases and crash recovery | Nicolas / Reginald | Durable database and Redis integration | Backlog |
| 2C Throttling, work quotas, server sessions, rotation/revocation/replay, topology-aware cookies/CSRF, recovery/verification | Nicolas / Newton / Reginald | Account scope and deployment topology | Backlog |
| 2D Docker ignores, npm ci, runtime/dev dependencies, production Compose/non-root, migration deployment, CI/API type consistency, README and health split | Nicolas / Diana / Newton | Phase 2 storage/reliability | Backlog |

## Phase 3 backlog (not implemented)

All items require representative baselines and before/after quality/performance measurements. No performance gain is claimed in Phase 1.

| Item | Owner | Dependencies | Status |
| --- | --- | --- | --- |
| Inspect live indexes/EXPLAIN; evaluate HNSW filtered recall and exact search | Nicolas | Representative corpus and disposable DB | Backlog |
| PostgreSQL full text plus semantic fusion/relevance evaluation | Nicolas | Retrieval correctness, labeled queries | Backlog |
| Bulk chunk insertion/RETURNING and statement-count benchmark | Nicolas | 100/1000-chunk benchmark | Backlog |
| Bounded background dataset profiling/cache by checksum/sheet | Nicolas | Memory and responsiveness baseline | Backlog |
| Token-aware long-document map/reduce and queued work | Nicolas | Stable source provenance | Backlog |
| Provider/token-aware embedding batching/cache and partial-progress optimization | Nicolas | Phase 1B durability | Backlog |
| OCR/research/analytics queues, real consumers, provider/GPU bounds | Nicolas / Diana | Concurrency benchmark | Backlog |
| Reduce SSE/detail polling; reconnect/backoff/subscription sharing | Nicolas / Newton | Durable snapshot verification | Backlog |
| Plotly trace subsets, math/Mermaid dependency narrowing | Newton | Visual/load benchmarks | Backlog |
| Measured streaming/columnar datasets while preserving full totals | Nicolas | Dataset scale/memory evidence | Backlog |
| Deadlines/token/cost budgets and version-aware cache policy | Nicolas / Newton | Provider cost metadata | Backlog |
| Queue/searchability/stage/provider/token/quality/export observability | Nicolas | Existing metrics | Backlog |
| Evaluate Jina threshold, retrieval/citations/entailment/contradictions/insufficiency separately | Nicolas / Reginald | Real labeled documents | Backlog |
| Adversarial document instructions, ambiguity/OCR/isolation evaluations | Nicolas / Reginald | Separate synthetic/human/live evidence | Backlog |

## Phase 4 backlog (not implemented; ordered)

| Item | Owner | Dependencies | Status |
| --- | --- | --- | --- |
| 4.1 Saved evidence notebook/matrix and cited literature export | Nicolas / Newton | Completed trust/reliability work | Backlog |
| 4.2 Reproducible experiment package/manifest | Nicolas / Newton | Validated execution and versions | Backlog |
| 4.3 Preview/approve immutable dataset transformations | Nicolas / Newton | Data-quality validation | Backlog |
| 4.4 Versioned reusable deliverable workflows/invalidation | Nicolas / Newton | Saved workflow inputs | Backlog |
| 4.5 Run diagnostics/budget UI | Newton / Nicolas | Cost/limit diagnostics | Backlog |
| 4.6 Completion/cancel/resume and appropriate durable checkpoints | Nicolas / Newton / Reginald | Idempotent claims and durable outputs | Backlog |

## Phase 5 backlog (not implemented)

| Item | Owner | Dependencies | Status |
| --- | --- | --- | --- |
| Validated array/table dispatcher images and restricted operations | Nicolas / Diana | Real target-compatible execution tests | Backlog |
| Deployable execution service independent of worker Docker daemon | Nicolas / Diana | Deployment topology | Backlog |
| Membership/RBAC, collaboration/reviews with access/revocation everywhere | Nicolas / Newton / Reginald | Authorization model | Backlog |
| Opt-in durable scheduled refresh, quotas/deduplication/cited diffs | Nicolas / Newton | Saved workflows, scheduler idempotency | Backlog |
| Larger datasets only against measured requirements | Nicolas | Scale benchmarks and limits | Backlog |

## Verification and review

### Confirmed findings and implemented contracts

The historical defects were present in current source: fabricated supporting metadata/random primary ID and commented persistence; provider-only embedding labels and unfiltered retrieval; rebuild placeholder; capped list-derived totals; inconsistent refresh/logout cleanup; an undefined download Promise mock; and exactly 17 E701 findings. These were fixed after confirmation. Existing ownership, relevance/claim gates, deterministic analytics and restricted launcher policies are preserved.

All paths below have `/api/v1` prefix and require authentication:

| Contract | Behavior |
| --- | --- |
| `POST /research/documents/{asset_id}/analyze?project_id=…` | Authorized source understanding cached by checksum/version/chunk fingerprint, title, goal/context, extractor/prompt/model identity and relevant endpoint/configuration. Legacy unkeyed metadata is not trusted as cache evidence. |
| `POST /research/documents/{asset_id}/cross-paper-analysis?project_id=…` | Requires 1–10 distinct supporting asset IDs and a goal; validates all sources before processing. Persists result, goal/context, versions and actual citation provenance; returns `artifact_id`, `created_at`, status and insufficiency reason. |
| `GET /research/documents/{asset_id}/cross-paper-analysis?project_id=…` | Latest persisted comparison or JSON null; reauthorizes all sources and flags changed source versions stale. |
| `GET /research/comparisons/{artifact_id}?project_id=…` | Restore a specific saved comparison, with the same authorization/version checks. |
| `GET /projects/summary?project_id=…` | Optional project scope; counts all owned records, including archived records. `documents` counts `asset_type=document`; `runs` counts research runs of every status; `grounded_answers` counts completed, explicitly grounded research runs with a trimmed nonempty answer. Counts use one SQL statement; recent items are separate requests. |
| `GET /projects/page`, `GET /assets/page`, `GET /research/runs/page` | `{items,next_cursor}`, limit 1–200 (default 50), descending timestamp plus UUID. Cursors bind owner, project and filters. Asset list omits heavy metadata/understanding; research list omits answers/citations. Existing offset/array routes remain available. Asset page accepts q/type/status/tags/project; research page accepts project, execution `status` and separate `grounding_status`. Grounded filtering uses the summary definition. |
| `GET /knowledge-base/readiness?project_id=…` | Full configured embedding identity, active space ID, total/searchable/legacy chunk counts, compatible/ready flags and durable rebuild status/counters/error. Empty projects are not ready. |
| `POST /knowledge-base/rebuild?project_id=…` | Creates/resumes a durable project/space rebuild, queues the worker and returns readiness (202). Dispatch failure is saved as retryable failed state without overwriting a live worker claim/result. Retry preserves successful staged batches. |

The primary comparison's missing information cannot be declared resolved by transferring a supporting value or attaching an unrelated primary quote. Model claims must be excerpts from validated cited quotes; locations/titles come from stored passages. Large/empty evidence sets return explicit insufficiency rather than silently fabricating or truncating evidence.

### Migrations, configuration and recovery

From `backend/` with the target environment configured, run `.venv/Scripts/python.exe -m alembic upgrade head` before starting the updated API and workers. New chain: `d4e5f6a7b8c0 -> 026_cross_paper_artifacts -> 027_embedding_spaces`.

- **026:** creates `document_understanding_cache`, `cross_paper_artifacts` and deterministic research-list index. No fabricated comparison or unkeyed understanding backfill is performed.
- **027:** adds nullable chunk identity/full space definition, nullable project active-space pointer, durable rebuild and staging tables. Existing vectors remain untouched with NULL identity; no provider/model identity is guessed. Retrieval excludes them until rebuilt. The staging column is 1024-dimensional, matching the current corpus; changing vector width requires a separate explicit schema migration.
- Configure API and worker with identical `EMBEDDING_MODEL`, `EMBEDDING_MODEL_VERSION`, `EMBEDDING_DIMENSION`, `EMBEDDING_DOCUMENT_TASK`, `EMBEDDING_QUERY_TASK`. Pin the version to the deployed model revision/digest and bump when weights change. Blank task values mean unconfigured; provider tasks are actually passed to the gateway. Settings are snapshotted at provider creation; restart API/workers after configuration changes.
- Select a project and use Overview's rebuild action. After failure/interruption, resume the same project/space job; successful content-hash-matched batches survive. Celery retries provider failures, completed redelivery performs no work, and expired claims are reclaimable. Direct manual dispatch is `celery -A app.workers.worker call workers.rebuild_embeddings --args='["REBUILD_UUID"]'` from a configured backend.
- During a rebuild, queries use only their configured **activated** space. A configuration switch returns no compatible evidence until activation. Staging vectors never enter retrieval. Validation plus all current chunk updates/project pointer/job completion commit atomically. Concurrent replacement is coordinated by a short project row lock; model calls do not hold rebuild database locks.
- Broker publication recovery in this phase is explicit retry plus Celery retry/redelivery. The durable outbox/periodic redispatch guarantee remains Phase 2B backlog; no exactly-once broker promise is made.
- Rollback: stop updated processes and back up persistent artifacts first. `alembic downgrade d4e5f6a7b8c0` drops comparison/cache/rebuild state and identity columns while preserving source assets/chunks/vectors. Re-upgrade recreates empty Phase 1 tables and leaves surviving vectors unverified. After a rebuild has overwritten active vectors, downgrade does **not** restore old-model vectors; re-embedding/backups are required. Do not run old retrieval code over a corpus containing vectors from switched spaces.

The representative migration script verifies a real pre-Phase-1 user/project/upload/chunk, 1024-vector, checksum, version and provenance across upgrade/downgrade/re-upgrade. Source data is preserved; artifact rollback loss is intentional and checked.

### Exact verification results

Commands are run from the indicated directory. The wrappers bound subprocess/service timeouts and clean up only their own disposable containers. Repeated failed baseline/diagnostic attempts are retained in logs, including unavailable Docker before the user started it.

| Directory / exact command | Exit | Result / evidence |
| --- | --- | --- |
| frontend: `npm test -- --reporter=dot` (baseline) | 1 | 325 assertions passed, one uncaught AssetsLibrary undefined `.catch()` error; `frontend-baseline.log`. |
| frontend: `npm run build` (baseline) | 0 | Build succeeded in 56.09s; `frontend-build-baseline.log`. |
| frontend: `npm test -- --reporter=dot` (final) | 0 | 54 files, **341 passed**, 16.88s; no uncaught errors. React act/router warnings remain. `frontend-final.log`. |
| frontend: `npm run build` (final) | 0 | TypeScript + Vite build; 44.77s Vite stage. Existing large-chunk warning remains. `frontend-build-final.log`. |
| frontend: `node_modules/.bin/openapi-typescript.cmd ../docs/improvement-evidence-2026-10-13/openapi-phase1.json -o src/types/api.d.ts` | 0 | Types regenerated from the actual FastAPI OpenAPI schema without a live server. |
| backend: `.venv/Scripts/python.exe scripts/check.py unit` (final) | 0 | **830 passed, 19 skipped, 468 deselected**, 9 warnings; 64.18s. `backend-unit-final.log`. OCR skips/service-dependent deselections are not passes. |
| backend: `.venv/Scripts/python.exe scripts/check.py integration` (broad final before last narrow review fixes) | 1 | **1279 passed, 9 failed, 27 skipped**, 9 warnings; 108.26s. All 9 failures require unavailable allowlisted expression-image digest; `backend-integration-final.log`. |
| backend: `.venv/Scripts/python.exe scripts/check.py integration tests/test_phase1_embeddings.py tests/test_phase1_pagination.py tests/test_embedding_spaces.py tests/test_cross_paper_real.py tests/test_cross_paper_persistence.py tests/test_test_environment.py tests/test_semantic_search.py tests/test_asset_processing_pipeline.py tests/test_research_citations.py tests/test_grounded_synthesis.py tests/test_analytics_dataset.py tests/test_reports_export.py tests/test_execution_front_door.py -ra` | 0 | **116 passed, 6 OCR skipped**, 11.30s. Real PG/pgvector/Redis; deterministic provider doubles. `backend-phase1-journeys-final.log`. |
| backend: `.venv/Scripts/python.exe scripts/check.py integration tests/test_phase1_embeddings.py tests/test_phase1_pagination.py tests/test_embedding_spaces.py tests/test_cross_paper_real.py tests/test_cross_paper_persistence.py -ra` | 0 | **41 passed**, 5.83s after mismatched-worker guard; `backend-phase1-review-final.log`. Later added blank-task pure regression separately passed. |
| backend: `.venv/Scripts/python.exe scripts/verify_phase1_migrations.py` | 0 | Representative legacy-data upgrade/downgrade/re-upgrade checks pass; `migration-representative-final.log`. |
| backend: `.venv/Scripts/python.exe -m pytest tests/test_embedding_spaces.py -q` | 0 | **8 passed**, 0.09s including blank-task settings regression. |
| backend: `.venv/Scripts/python.exe -m ruff check app --select E701` | 0 | All checks passed; 17 original findings fixed. |
| backend: `.venv/Scripts/python.exe -m ruff check app/core/pagination.py app/modules/knowledge_base app/modules/projects/router.py app/modules/assets/router.py app/workers/scheduler.py tests/test_embedding_spaces.py tests/test_phase1_embeddings.py tests/test_phase1_pagination.py --output-format concise` | 0 | Focused changed-feature lint passed. |
| root: `git diff --check` | 0 | No whitespace errors; Windows line-ending notices only. |
| backend: `.venv/Scripts/python.exe -m pytest tests/test_cross_paper_real.py tests/test_embedding_spaces.py -q -m 'not integration'` (Reginald independent final) | 0 | **30 passed**, 1 warning, 1.60s; review artifact records independent checks. |

Earlier backend fixture diagnostics produced 34 unit failures when isolation exposed mock credentials that relied on developer settings, and 8 integration failures from unverified-vector fixtures/missing mock credential. Targeted test fixture changes supplied explicit fake credentials and explicit verified space identities. Separate-process Docker fixture settings were corrected. No production credential check/retrieval filter/assertion was weakened.

### Review and unresolved verification

[Reginald's independent review](phase1-reginald-review.md) found and verified corrections for: unrelated primary quotes falsely resolving missing values; incorrect grounded history filter; queued rebuild polling; stale SSE callbacks; offline tests incorrectly grouped with integration; and misconfigured-worker status regression. All six material source findings are resolved with regression coverage. The coordinator also corrected session-scoped persona updates and retryable broker-dispatch error presentation.

Current acceptance limits:

1. **Approved expression launcher image unavailable.** The production pin `aikdap-exec-class-expr@sha256:cd631bedb89e1ed7d1b5f69883619d5547336f8d34bc8bc670768c4d9e3eae6e` is absent. The existing Dockerfile builds image `sha256:0a346365d3faf833dd22be60d37cf67176ec04e5bfc21365ac6efda3781db440`, and a separately restricted direct dispatcher diagnostic returns `14.0`. This is not the production-approved launch path. Nine broad-suite failures remain; policy/allowlist are unchanged. Image publication/pinning belongs to the tracked Phase 5 work.
2. **Live provider quality unavailable.** A bounded local Ollama `/api/tags` probe failed with URLError; live Ollama test is opt-in and skipped. Two-paper contradictions, exact citation binding and understanding cache behavior were verified using persisted passage fixtures and deterministic providers, not live scientific-document accuracy. Hosted model credentials/production corpus were not used for these checks.
3. **OCR/symlink limits.** Full integration skips 25 OCR cases because tesseract is not on PATH, one live Ollama case and one Windows symlink privilege case. Do not count them as passes. Real Alpine lifecycle tests were run after pulling Alpine; separate-process lifecycle regressions pass.
4. **Browser/live deployment limits.** Component tests verify selected asset/chunk IDs reach the source viewer, restore after remount, stale citation blocking, canvas fallback and scroll/navigation contracts. Actual PDF highlighting/graphics/download behavior in a real browser, hosted storage and production deployment are not certified. No source/corpus benchmark or performance improvement is claimed.
5. **Embedding crash timing.** Durable expired-lease/concurrent-claim recovery is verified on PostgreSQL; a hard Celery worker kill plus the broker's real visibility/redelivery delay has not been timed. A real broker/worker provider-failure retry diagnostic is recorded separately below.

Phase 2 dependencies include an object store shared by API/worker, periodic transactional-outbox recovery, account/session controls and deployment/CI hygiene. These were not pulled into Phase 1.

### Changed-file map

- Backend comparison: `app/agents/planner/{analysis,cross_paper,cross_paper_evidence}.py`, research `{models,schemas,repository,service,router}.py`, migration 026 and cross-paper regression modules.
- Backend embedding/readiness: knowledge-base `{models,schemas,embeddings,spaces,rebuild,repository,service,router}.py`, project active-space model, asset pipeline, worker tasks/scheduler, migration 027, settings/gateway and embedding regressions.
- Backend counts/history: `app/core/pagination.py`, projects `{router,schemas,repository}.py`, assets `{router,repository}.py`, research page service/repository/router and 107-record API regression.
- Frontend integration: paged services/hooks/LoadMore, Dashboard/Projects/Research/ProjectDetail, assets/documents/analytics/evidence workspace, comparison/readiness panels, generated API types; centralized auth/client/session/SSE/persona boundaries and regression suites.
- Diagnostics: backend `tests/conftest.py`, pytest marker/settings/provider fixture updates, `scripts/{check,verify_phase1_migrations,verify_embedding_worker}.py`, Promise/browser stubs, focused Ruff syntax edits, `.env.example`, relevant Render embedding config/worker documentation, this progress record and Reginald review. Original prompt/review files are preserved.

### Real embedding worker diagnostic

From backend, `.venv/Scripts/python.exe scripts/verify_embedding_worker.py --worker-log ../docs/improvement-evidence-2026-10-13/embedding-worker-process.log` exits **0**, 33.415s. `embedding-worker-verification-final.log` records the assertions; the process log retains actual Celery task/retry IDs. The deterministic provider injects one failure in the second batch; only its scheduling delay is accelerated to one second in the diagnostic worker. Real broker delivery and one Celery retry produce batch sizes `[32,32,32,1]`, task retry counts `[0,0,1,1]`, and durable per-item attempts `32×1,32×2,1×1`. The successful first 32 chunks are not repeated. The old space remains active with no target vectors searchable before completion; all 65 activate together and readiness is 65/65. Republishing the completed rebuild through Redis performs no extra embedding calls. Worker/results are cleaned before disposable service teardown; final exit has no uncaught/destructor shutdown error. No live-model or hard-worker-kill claim is made.

The first diagnostic exposed Celery reading an ambient result-backend variable outside Pydantic's isolated singleton. The script now explicitly scopes both parent and worker environments to its disposable Redis. The failed attempt remains in `embedding-worker-client-env-diagnosis.log`.

### Local project-list runtime fix after user report

The user showed the development dashboard with correct aggregate/research data and a failing Recent Projects card. Docker backend traceback confirmed `UndefinedColumn: projects.active_embedding_space_id does not exist`; its local Compose database (`postgres`, `/aikdap`, development) was still at `d4e5f6a7b8c0` while mounted source had Phase 1 models. This was a missing local migration, not lost projects or an authorization error.

A local custom-format backup was created at `backend/.e2e_scratch_phase1_migration/aikdap-before-phase1.dump` (ignored, 26,723,181 bytes). Exact commands: `docker exec aikdap_backend alembic current` exit 0 (old revision); `docker exec aikdap_postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" -Fc -f /tmp/aikdap-before-phase1.dump'` exit 0; `docker cp aikdap_postgres:/tmp/aikdap-before-phase1.dump backend/.e2e_scratch_phase1_migration/aikdap-before-phase1.dump` exit 0; `docker exec aikdap_backend alembic upgrade head` exit 0 applied 026/027. No production database was contacted. Existing local app containers were used only for this user-requested fix, after isolated verification had completed.

Authenticated local HTTP verification after migration returned project page limit 4 with HTTP 200 and four records, page limit 50 with HTTP 200 and nine records, and summary HTTP 200 with nine projects. Tokens were kept inside the backend verification process and not logged. Local worker was restarted to load current source. Existing legacy vectors remain unverified as designed; rebuild them using an available configured provider before claiming search readiness.
