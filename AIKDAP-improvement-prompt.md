# AIKDAP improvement implementation prompt

Implement the improvements below in the existing AIKDAP repository. This is an implementation task: inspect the current code, make focused changes, validate them, and deliver reviewable results.

## Scope for this run

ACTIVE_PHASE = 1

Implement the entire active phase, including its tests, migrations, documentation, and review. Keep later phases as a tracked backlog. When I request another phase, update ACTIVE_PHASE and apply the same workflow. If I explicitly request all phases, work through them sequentially and preserve phase checkpoints; do not combine everything into an unreviewable change.

Repository: `C:\Users\Divya Bangde\OneDrive\Desktop\My projects\AIKDAP`.

Source review: “AIKDAP project review and implementation roadmap,” dated 13 October 2026. The original report is available at `C:\Users\Divya Bangde\.codex\attachments\e2b82b2a-d3a1-465d-9edf-751dd24f1a09\Pasted text.txt` in this workspace. Read it when available. The requirements below also provide a standalone implementation brief.

Treat report findings, paths, and line numbers as investigation leads. Confirm them against current code. Distinguish confirmed defects, conditional risks, already-fixed issues, and checks that require unavailable services. Historical test results and synthetic evaluations do not establish current production readiness.

## Architecture and ownership

Read applicable AGENTS.md instructions first. Preserve React, FastAPI, LangGraph, Celery, PostgreSQL/pgvector, Redis, and the restricted execution launcher. Retain existing capabilities, ownership checks, evidence provenance, relevance gates, deterministic analytics validation, and execution restrictions. Use targeted refactoring as modules change; do not rewrite the architecture or large files merely because they are large.

Use the following roles, respecting their constraints. You may delegate independent work to subagents when available, with explicit file ownership and agreed API contracts. If subagents are unavailable, execute the roles sequentially and identify whose responsibility each change falls under.

- Newton: React UI, client services, user-scoped caching, pagination, charts, evidence workspace, and frontend checks. No backend logic. If an endpoint is missing, request its contract from Nicolas and use clearly identified development mocks until the real integration is ready. A mock does not satisfy acceptance.
- Nicolas: APIs, schemas, database migrations, storage, retrieval, ML workflows, workers, deployment configuration, and backend checks. No UI changes.
- Diana: reproduce failing checks and diagnose dependency, settings, test-environment, runtime, OCR, Docker, CUDA, or GPU problems. Make exact targeted fixes. Do not assign new product features to Diana.
- Reginald: independently review Newton/Nicolas changes for authorization, contracts, evidence fidelity, concurrency, recovery, performance, and edge cases. Explain the impact and reason for each actionable finding. Implementation owners resolve findings.

The coordinator integrates changes and owns the progress record. Define backend contracts before dependent UI work. Do not have agents edit the same files concurrently or share one SQLAlchemy AsyncSession between parallel operations.

## Working method

1. Inspect repository state and preserve existing user changes. Reproduce relevant baseline checks with bounded timeouts before editing. Identify actual tools, migration conventions, and test commands from the repository.
2. Maintain `docs/improvement-progress.md` with every roadmap item, owner, dependencies, status, acceptance evidence, and blockers. Mark an item complete only when its acceptance criteria are met. Record existing fixes with supporting evidence rather than reimplementing them.
3. Implement small coherent units, including API/client integration and schema migrations. Document migration/backfill behavior, compatibility, configuration, and recovery or rollback for persistent changes.
4. Add meaningful regression tests for changed correctness and security behavior. Run relevant checks, then the active phase's integration and end-to-end gates. Record exact commands, exit codes, and limitations. Do not hide uncaught errors, weaken assertions, or describe mocks as live verification.
5. Have Reginald review each unit. Resolve material findings and rerun affected checks. Proceed autonomously on reversible local work; ask only for missing information that prevents a correct implementation or authorization for external production changes.
6. If credentials, Docker, or external services are unavailable, complete independent work and local substitutes, clearly identify remaining real integration checks, and do not mark those checks passed. Keep secrets out of logs and committed files. Do not deploy to production as part of a local implementation run.

## Phase 1 — Trust, correctness, and clean verification

### 1A. Real cross-paper analysis

Inspect `backend/app/modules/research/service.py` and `backend/app/agents/planner/cross_paper.py`. Remove fabricated supporting titles/methodologies and random replacement primary-paper IDs.

- Authorize every primary/supporting asset against the selected project and user before loading or processing it.
- Load or compute real structured document understanding. Retain actual asset/chunk IDs and validate referenced passages and source membership.
- Cache understanding using document checksum, goal/context, extractor version, model identity, and prompt version. Invalidate on any relevant input change.
- Persist the comparison, goal, source versions, provenance, and result as a reusable artifact; provide the API/client behavior needed to restore it after refresh.
- Preserve explicit insufficient-evidence states. Do not invent methods, findings, citations, or supporting metadata.

Acceptance: two papers with conflicting results produce a comparison grounded in real passages; citations open the right source; refresh restores it; foreign-project and inaccessible assets are rejected; version changes invalidate stale cache entries.

### 1B. Embedding-space compatibility and rebuilds

Inspect knowledge chunk models/repositories, embedding gateways, and `workers.rebuild_embeddings`.

- Persist an embedding-space identity covering provider, model, version, dimensions, and task configuration that affects the vector space.
- Restrict retrieval to the compatible active space. Equal dimensions do not establish compatibility between BGE-M3 and Jina.
- Handle legacy vectors conservatively; do not assign an unverified space identity to existing data.
- Implement resumable re-embedding with durable progress, retries, validation, and atomic activation. Define query behavior during a rebuild and prevent mixed-space retrieval.
- Surface compatibility and rebuild/readiness status through the relevant APIs and UI.

Acceptance: switching between different 1024-dimensional spaces never searches incompatible vectors; interruption resumes without repeating successful work; failed validation does not activate a partial space; readiness reflects actual searchable coverage.

### 1C. Accurate totals and paginated history

- Add owner-scoped aggregate summary counts, with documented definitions for assets/documents, runs, and grounded answers. Fetch recent items separately.
- Add pagination to project, asset, and research list APIs and UIs. Use compact summaries and deterministic timestamp-plus-ID ordering; prefer cursor pagination for growing histories.
- Update Dashboard to use aggregate totals, not first-page array lengths. Include loading, empty, error, and subsequent-page states.

Acceptance: a workspace with more than 100 records has correct totals and accessible history; tied timestamps and page boundaries cause no missing or duplicated records in a stable dataset; users only see authorized data.

### 1D. Consistent session invalidation

- Centralize logout/session-loss handling for explicit logout, rejected refresh, and other invalidation paths.
- Cancel outstanding user requests, clear user-scoped caches/state, scope query keys by user/session where appropriate, and prevent late responses from restoring stale data.

Acceptance: user A -> refresh failure -> user B login cannot display A's cached profile/projects/assets; an in-flight A request resolving after logout cannot repopulate B's session. Backend authorization remains enforced.

### 1E. Clean baseline checks

- Diagnose the AssetsLibrary download mock returning undefined before `.catch()`. Make mocks match real Promise contracts; fix real production behavior separately if it is also incorrect.
- Resolve relevant canvas/scroll/navigation test gaps with deliberate stubs or browser coverage.
- Isolate unit-test settings/provider-health state and unintended network calls; give database fixtures bounded failures and use disposable services for integration tests.
- Address confirmed Ruff E701 findings with focused edits. Do not assume the historical count is still correct.

Acceptance: relevant frontend tests and build exit 0 without uncaught errors; isolated backend unit tests exit 0; remaining service-dependent checks are listed explicitly.

## Phase 2 — Deployment reliability and account controls

### 2A. Shared durable storage

Implement an object-storage provider behind the existing StorageProvider interface. Prefer an S3-compatible implementation unless existing requirements identify another provider. Keep local development support.

Use stable object keys in the database and shared bucket/configuration for API and workers. Support authorized reads/PDF viewing, bounded transfers, safe key handling, cleanup, retries, and documented treatment of existing local uploads. Update deployment configuration and secret-free environment examples. An API-only persistent disk cannot give a separate worker access.

Acceptance: upload -> separate worker ingestion -> PDF viewing -> API/worker replacement still works with a real file. Unauthorized access fails. Interrupted uploads and deletion leave consistent records/objects or documented retryable cleanup.

### 2B. Transactional outbox and idempotent execution

Persist an outbox entry in the same transaction as upload/research/analytics job creation. Publish with retries and dispatch bookkeeping through a periodic process that recovers without a worker restart.

Use atomic claims, terminal-run guards, leases/recovery, and idempotent side effects. Reuse existing execution claim/reconciliation patterns where suitable. Protect concurrent asset chunk replacement. Do not hold database locks during model calls.

Broker publication is at least once: a crash after publication but before dispatch bookkeeping can produce duplicates. Design for one effective execution/result using durable claims and idempotency; do not promise exactly-once broker delivery.

Acceptance: submit with Redis unavailable, restore Redis, and observe eventual processing without resubmission/restart. Independently test duplicate delivery, simultaneous claims, worker death, lease recovery, and publication/bookkeeping crash windows; no duplicated effective outputs or inconsistent status.

### 2C. Authentication and work limits

Add login/registration throttling, per-user concurrency/work quotas, server-side sessions, refresh-token rotation, replay detection, and revocation. Avoid account enumeration.

Evaluate an HttpOnly refresh cookie against the actual frontend/API topology; implement explicit CSRF, SameSite, Secure, and CORS behavior if adopted. Never make cookie assumptions that break cross-origin deployment. Add password recovery/email verification when intended account scope requires them; use a local mail adapter when a real service is unavailable and document configuration.

Acceptance: replay/revocation tests, concurrent refresh behavior, throttling/quota tests, and session-loss UI behavior pass. Required cookie/CSRF controls work for the configured topology.

### 2D. Deployment and engineering hygiene

- Add backend/frontend .dockerignore files excluding secrets, environments, uploads, dependencies, scratch data, and test outputs while retaining required build inputs.
- Use npm ci with the lockfile. Separate Python runtime/development requirements.
- Provide production Compose settings without reload/source mounts; use non-root execution where supported.
- Add an explicit migration deployment step and CI for frontend build/lint, isolated unit tests, disposable PostgreSQL/Redis integration tests, migration checks, and generated API-type consistency.
- Populate the root README and correct stale worker documentation with setup/run/test/deploy/recovery commands.
- Distinguish process liveness, critical dependency readiness, and optional provider diagnostics while preserving documented compatibility for existing health consumers.

Acceptance: reproducible builds and CI succeed; migration from representative existing data works; independent services share durable storage; unavailable critical dependencies produce actionable readiness status.

## Phase 3 — Measured performance and evaluation quality

Establish representative baselines before optimizing. Record dataset/query sizes, environment, providers, warm/cold conditions, p50/p95, throughput, resource usage, and retrieval/evidence quality. Historical bundle sizes and calibration fixtures are comparison leads, not current measurements.

- Inspect live/disposable database indexes and EXPLAIN (ANALYZE, BUFFERS). Benchmark HNSW/vector_cosine_ops when corpus sizes justify it. Test owner/project filtering, recall@k, and iterative scans; retain exact search where it performs better.
- Add PostgreSQL full-text retrieval alongside semantic retrieval, fuse candidates, and reuse reranking/relevance gates. Evaluate exact phrases, abbreviations, names, IDs, equations, and foreign-project isolation.
- Remove unnecessary per-row chunk refreshes; use bulk insert/RETURNING where appropriate. Compare statement counts and wall time for 100/1000 chunks.
- Move CPU-intensive profiling off async request execution into bounded threads/tasks. Prepare/cache profiles by checksum and sheet, preserving bounds and concurrent API responsiveness.
- Add token-aware section map/reduce for long documents instead of sending up to 10,000 chunks in one prompt. Preserve stable references; queue long work.
- Make embedding batches provider/token-aware and persist partial progress. Cache by content and full embedding space; verify injected failures only repeat unfinished batches.
- Separate metadata/OCR, research, and analytics queues with actual consuming workers, bounded concurrency, and provider/GPU limits. Benchmark local Ollama concurrency before increasing it.
- Reduce redundant SSE/detail polling and database reads while preserving durable snapshots, reconnect, and polling fallback; back off inactive polling/share subscriptions where appropriate.
- Reduce Plotly bundles using registered trace subsets or separate ordinary/3D charts. Narrow math/Mermaid dependencies where practical; preserve representative visual behavior and existing lazy loading.
- Retain bounded pandas workloads. Introduce streaming/columnar processing only when measured memory/scale requirements justify it, verifying full-data totals.
- Add run deadlines/token budgets, estimated/actual cost records, and version-aware caches. Stop new calls when budgets are exhausted and persist usable completed work.
- Measure queue age, time until searchable, stage durations, provider/run failures, token usage, retrieval quality, and export success in existing observability.
- Re-evaluate hosted Jina reranking and its provisional 0.3 threshold using representative real documents. Track recall@k, citation correctness, claim entailment, contradiction accuracy, and insufficiency decisions separately.
- Add adversarial document instructions, ambiguous questions, OCR damage, and foreign-project references. Treat document content as evidence, not executable agent instructions. Keep synthetic fixtures, human labels, and live-model results separate.

Acceptance: provide before/after measurements and quality checks for each optimization. Do not claim gains without measurements or publish one accuracy percentage combining unrelated evaluation types. Document any quality/performance tradeoff.

## Phase 4 — Product depth and reusable evidence

Implement features in this order, building on completed reliability work:

1. Saved evidence notebook and real paper comparison matrix: save claims/passages, methods/data/results/limitations; retain source versions, provenance, user edits, authorization, persistence, and cited literature-review export.
2. Reproducible experiment package: export equations, variables, dataset checksums, source references, test cases/sweeps, model/config/environment versions, execution results, and a reproducibility manifest without secrets. Verify a supported example can be reproduced from the package.
3. Data-quality workflow: explain missing values/duplicates/suspicious types; preview and approve bounded declarative transformations into a new immutable dataset version. Preserve original data and transformation history.
4. Reusable deliverable workflows: saved literature-review -> evidence-matrix -> synopsis and dataset -> analysis -> executive-report flows, with versioned inputs/configuration and explicit invalidation.
5. Run diagnostics/budget UI: failed step, actionable recovery options, provider identity, token/cost/resource limits, and truthful run state.
6. Completion/cancellation/resume: propagate cancellation, prevent further side effects where feasible, preserve completed outputs, and resume durable work. Add graph checkpoints only where required by actual resumability semantics.

Acceptance: integrated UI/API journeys survive refresh; evidence opens real authorized passages; exports have complete provenance; previews do not mutate originals; canceled/completed runs cannot be accidentally restarted by duplicate messages; resumed work preserves validated completed steps.

## Phase 5 — Scale, collaboration, scheduling, and execution

- Complete approved array/table execution operations. Replace test-only image digests with built, validated dispatcher images; retain operation allowlists, network restrictions, resource/time bounds, and output validation.
- Provide/document a deployable execution service. The hosted worker must not assume Docker-daemon access exists. Exercise real supported expression/array/table execution in the target-compatible environment before advertising availability.
- Add project membership/RBAC before collaboration, assignments, reviewer comments, and approvals. Apply authorization to every read/write, download, SSE stream, export, retrieval, and worker path; handle revoked membership.
- Add opt-in scheduled literature/dataset refresh with durable scheduling, quotas, idempotency, cancellation, source deduplication, and cited difference reports. Reuse saved workflow configurations and record versions.
- Extend large-dataset execution only against measured requirements, preserving limits, accuracy, and reproducibility.

Acceptance: a real deployment-compatible execution integration passes; foreign-project/member-revocation tests pass; duplicate scheduler delivery does not duplicate effective runs; new-source refresh produces a cited change report and respects budgets.

## Final verification and handoff for each phase

Cover the journeys touched by the active phase: upload -> searchable chunks -> grounded answer -> citation viewer, conflicting two-paper comparison and restore, analytics totals, report export, and supported expression execution. Use real disposable services for applicable integration tests. Mark OCR, live-provider, Docker, hosted storage, and production checks separately when unavailable.

Deliver:

- Implemented behavior and why it resolves each confirmed issue.
- Changed files, API contracts, migrations/backfills, and configuration instructions.
- Exact check commands, exit codes, regression/integration evidence, and current benchmark results where applicable.
- Reginald's review findings and their resolution.
- Remaining blockers, unverified paths, and the next phase's dependencies in the progress record.

Start now with the active phase. Do not finish with only a plan. Do not mark later phases or unperformed live checks complete.
