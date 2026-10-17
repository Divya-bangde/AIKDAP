# AIKDAP project review and implementation roadmap

Review date: 13 October 2026. Scope: application architecture, frontend, backend modules, research and analytics agents, ingestion and retrieval, execution launcher, migrations, workers, deployment configuration, tests, and stored evaluation results.

## Assessment

AIKDAP has a substantial foundation for an evidence-based research and analytics workspace. Its strongest product direction is a complete, traceable workflow: upload sources, investigate a question, inspect supporting evidence, compare papers, produce a report or experiment plan, and reuse the output in the project.

The highest-value next work is completing and hardening this workflow. Preserve the existing React/FastAPI/LangGraph/Celery/PostgreSQL architecture. There is no evidence from this review that an architecture rewrite would improve the product.

This is a cross-project source review with selected checks, not a certification that every endpoint works in a live deployment. No application code was changed. No cloud inference was deliberately invoked, and the live production database was not tested. Runtime and scale claims below distinguish measured results from source-based risks.

## Existing capabilities worth preserving

| Area | Implemented foundation |
| --- | --- |
| Workspace | Authentication, project ownership, personal/project personas, command center, project assets and history |
| Ingestion | Multiple document formats, text extraction, OCR support, structured chunk provenance, PDF source positions, checksum-based duplicate detection |
| Retrieval | pgvector cosine search, candidate over-retrieval, reranking, calibrated relevance gating, query decomposition and reformulation |
| Research | LangGraph orchestration, evidence-grounded synthesis, claim verification, source mix, explicit insufficient-evidence/unsourced states, follow-ups |
| Papers | OpenAlex discovery/import, paper references and paper map |
| Outputs | Synopsis and build-plan generation, report exports, reusable generated assets |
| Analytics | CSV/XLSX profiling, bounded declarative plans, deterministic pandas execution, charts, narrative number verification, Kaggle integration |
| Experiments | Equation interpretation, plan editing, test cases and sweeps; expression execution through a restricted Docker launcher |
| Reliability | Typed LLM gateway, retries/fallbacks, Redis-backed provider health, durable workflow steps, SSE with polling fallback, execution reconciliation |
| Frontend | Route and heavy-feature lazy loading, TanStack Query, component tests, theme support and reduced-motion handling |

Some adjacent paths remain incomplete, as described below. A registered endpoint or a passing mocked test does not prove the complete user workflow.

## Correctness and deployment priorities

### 1. Complete real cross-paper analysis — high priority

**Evidence:** `backend/app/modules/research/service.py:208` assembles supporting papers using a generated title and the literal methodology `Alternative methodology`; it does not load their extracted facts. Comparison persistence is commented out. `backend/app/agents/planner/cross_paper.py:78` assigns a new random UUID to the primary paper rather than its actual asset ID.

**Impact:** The comparison does not represent the supporting documents the user selected, cannot reliably link the primary document back to an asset, and is not saved for later reuse.

**Implementation:** Verify every supporting asset belongs to the selected project and user. Load or compute its structured understanding, retain real asset/chunk IDs, pass those facts to the reducer, validate every referenced source, and persist a comparison artifact with source versions and goal. Cache understanding by document checksum, goal/context and extractor/model/prompt version. Reuse it only when those inputs match.

**Acceptance:** Two papers with conflicting results produce a comparison linked to real passages; refresh restores it; an inaccessible supporting asset is rejected; no fabricated supporting metadata enters the prompt.

### 2. Add durable storage shared by API and worker — high priority

**Evidence:** `backend/app/modules/assets/storage.py:95` always returns local filesystem storage. `render.yaml` deploys the API and worker as separate services without shared object storage. Local Compose works differently because both services bind-mount the same backend directory.

**Impact:** With the checked-in hosted configuration, an upload written by the API is not made available to the separate worker through this storage implementation. Local files also lack persistence guarantees across service replacements.

**Implementation:** Add a provider for object storage behind the existing `StorageProvider` interface. Store stable object keys in the database, use the same bucket/configuration in API and worker, and implement authenticated reads, bounded downloads and cleanup. Verify upload → worker ingestion → PDF viewing → restart with a real file.

Render documents that a persistent disk belongs to one service instance and cannot be accessed by another service; adding an API disk alone would not fix worker access. [Render disk documentation](https://render.com/docs/disks)

### 3. Enforce embedding-space compatibility — high priority

**Evidence:** Knowledge chunks store an embedding provider enum but no full model/version identity. `backend/app/modules/knowledge_base/repository.py:76` filters for completed non-null embeddings but not provider/model compatibility. Local configuration selects BGE-M3 while hosted configuration selects Jina. Both use 1024 dimensions. `workers.rebuild_embeddings` currently returns `not_implemented`.

**Impact:** A model switch can leave old vectors eligible for comparison with queries in a different vector space. Matching dimensions do not make those vectors compatible. This is a conditional source-level risk; this review did not inspect the deployed corpus.

**Implementation:** Persist an embedding-space identifier including provider, model, version, dimensions and relevant task configuration. Search only compatible chunks. Implement resumable re-embedding, progress reporting, failure retry and atomic activation of the new space after validation. Make model compatibility visible in health/project readiness.

### 4. Correct dashboard totals and implement list pagination — high priority

**Evidence:** Projects, assets and research list routes default to 100 records. Frontend list services do not expose pagination. `frontend/src/pages/Dashboard.tsx:92` and `:113` derive totals from returned array lengths. Grounded answers and document counts are also computed from these limited lists.

**Impact:** Large workspaces have understated totals and inaccessible history beyond the first page.

**Implementation:** Add an aggregate summary endpoint with real counts and separate recent-item queries. Add pagination to list UIs. Prefer stable ordering by timestamp plus ID, and use cursor pagination when history becomes large. Return compact summaries rather than full result metadata in lists.

### 5. Close the database-commit/task-publication gap — high priority

**Evidence:** Research creation, upload processing and analytics creation commit database records before calling Celery `.delay()`. This is necessary for worker visibility but leaves a gap if broker publication fails. Startup reconciliation exists for several run types, but it is not a durable publication guarantee.

`ResearchExecutionService.execute` at `backend/app/modules/research/service.py:467` also sets any existing run to `RUNNING` without an atomic claim or a completed-run guard. Combined with late acknowledgements, a redelivered message can execute a run again. Replacing asset chunks is idempotent for a sequential rerun, but it is not an atomic protection against two concurrent processors.

**Implementation:** Persist an outbox record in the same transaction as the job, publish it with retry, and mark it dispatched. Add atomic execution claims and explicit idempotency to tasks that can be redelivered. Existing execution-job claims/reconciliation are useful patterns to reuse. Avoid holding database locks through model calls.

**Acceptance:** Stop Redis during submission, restore it, and verify that the job dispatches once without requiring another user request or worker restart. Test worker death and duplicate delivery independently.

### 6. Make session invalidation consistent — medium/high priority

**Evidence:** Explicit logout in `frontend/src/hooks/useAuth.ts` clears TanStack Query. Refresh failure in `frontend/src/services/client.ts:109` clears only the auth store. Auth and workspace query keys are not scoped by user identity.

**Impact:** Cached profile/workspace data can survive an automatic logout and be reused briefly after a different login. This needs a two-user regression test; it is not evidence of backend authorization bypass.

**Implementation:** Centralize invalidation so it cancels outstanding requests and clears all user-scoped caches on every session loss. Scope cache keys by user/session where appropriate. Test user A → rejected refresh → user B login and an in-flight request resolving after logout.

### 7. Strengthen authentication and request controls — medium/high priority

**Evidence:** Tokens are persisted in local/session storage; refresh tokens are stateless and remain valid after replacement until expiry. No application rate limiter or refresh-token revocation/session table was found in the reviewed auth paths.

**Implementation:** Add login/registration throttling, per-user concurrency and work quotas, refresh rotation with replay detection, and server-side session revocation. Consider an HttpOnly refresh cookie with explicit CSRF/SameSite/CORS handling for the chosen frontend/API topology. Add password recovery and email verification when accounts move beyond a controlled demo.

## Performance optimizations

| Priority | Observation | Proposed improvement | How to verify |
| --- | --- | --- | --- |
| High | Source and migrations contain no HNSW/IVFFlat index for cosine retrieval | Inspect live indexes and `EXPLAIN (ANALYZE, BUFFERS)` first; benchmark HNSW with `vector_cosine_ops` when corpus size justifies it | p50/p95 retrieval time and recall@k on real project-filtered queries |
| High | Retrieval is semantic-first; no chunk full-text retrieval path was found | Combine PostgreSQL full-text retrieval and vectors, fuse candidates, then reuse reranking/relevance gates | Compare recall for abbreviations, exact phrases, names, IDs and equations |
| High | `KnowledgeChunkRepository.bulk_create` flushes then refreshes each chunk separately | Remove unnecessary per-row refreshes where defaults are already returned; use bulk insert/RETURNING when needed | SQL statements per 100/1000 chunks and ingestion wall time |
| High | First dataset profiling parses and profiles pandas data directly inside an async request | Move CPU work to a bounded thread/task; prepare profiles during ingestion and cache by checksum plus sheet | API latency during a large profile request; concurrent request responsiveness |
| Medium | Full document analysis loads up to 10,000 chunks into one prompt | Add token-aware limits and section map/reduce with stable source references; queue long analysis | Large-paper success rate, tokens, latency and evidence fidelity |
| Medium | Embedding gateway already batches 64 texts, but partial successful batches are not persisted if a later batch fails | Make batching token/provider-aware and persist resumable batch progress; cache embeddings by content plus embedding space | Calls repeated after injected batch failure; time until searchable |
| Medium | Heavy metadata, OCR, research and analytics share the default Celery routing | Separate task queues, bounded worker concurrency and provider/GPU concurrency limits | Queue wait times under simultaneous upload, research and analytics workloads |
| Medium | SSE server also polls the database every 2 seconds per stream; run detail polling continues in some views | Keep durable snapshot/reconnect behavior while reducing redundant reads; back off inactive polling and share subscriptions | Database reads and transferred bytes per active run |
| Medium | Plotly chunk measured 4,279.53 kB minified, 1,324.70 kB gzip | Use an explicitly registered trace subset or separate ordinary charts from 3D; keep current lazy loading | Feature load/download time, chunk gzip size and representative chart checks |
| Medium | Equation chunk measured 682.56 kB; Mermaid renderer 659.90 kB | Inspect dependency contribution; import narrower math functionality and only necessary diagram capabilities when practical | Chunk sizes and time until interaction on a slower device |
| Medium | Analytics reads bytes and processes bounded in-memory frames | Preserve pandas for current bounded workloads; evaluate streaming/columnar processing when measurements show memory pressure | Peak memory, accurate totals, parse time and concurrent-job throughput |
| Medium | Per-step token counts already exist, but no comprehensive run budget/cost policy was found | Add run deadlines, token budgets, actual/estimated cost records and cache keys containing source/prompt/model versions | Calls/tokens per successful deliverable, timeout rates and stale-cache tests |

Approximate vector indexing can reduce recall after project/owner filters; evaluate filtered queries and iterative scans before enabling it. Retain exact search when a small project corpus makes it faster or more accurate. [pgvector indexing/filtering documentation](https://github.com/pgvector/pgvector#filtering)

Celery supports routing tasks to queues with separate consumers. Introduce queues together with workers that actually consume them. [Celery routing documentation](https://docs.celeryq.dev/en/stable/userguide/routing.html)

Concurrency is already used for decomposed retrieval. Any additional parallel work must use independent database sessions; do not share the run's `AsyncSession` between concurrent tasks. The code records that local Ollama requests serialized during an earlier measurement, so increasing local model concurrency needs new measurements. [SQLAlchemy asyncio documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

## Valuable implementations after these fixes

| Order | Feature | User outcome | Existing foundation | Relative effort |
| --- | --- | --- | --- | --- |
| 1 | Saved evidence notebook and real paper comparison matrix | Save claims/passages, compare methods/data/results/limitations, generate a cited literature review | Citation/source viewer, document understanding, cross-paper reducer, report export | Medium |
| 2 | Reproducible experiment package | Export equations, variable values, source references, datasets, test cases, model/config versions and execution results together | Experiment plans, variants, test cases, restricted execution, assets | Medium/high |
| 3 | Data-quality workflow | Explain missing values, duplicates and suspicious types; preview and approve transformations into a new dataset version | Dataset profile, deterministic plan validator/executor, generated assets | Medium |
| 4 | Reusable deliverable workflows | Repeat literature review → evidence matrix → synopsis, or dataset → analysis → executive report | Graph registries, personas, history, reports | Medium |
| 5 | Run diagnostics and budget controls | See the failed step, recovery options, provider used, token usage and resource limits | Workflow steps, provider health, usage tracking, reconciliation | Medium |
| 6 | Completion/cancellation controls and resumable work | Recover or stop long work and preserve completed outputs | Durable runs and execution attempts; graph checkpoints would be additional work | Medium/high |
| 7 | Complete array/table execution capabilities | Execute the approved array/table operations that currently lack real image digests | Closed operation catalog, resource policy, execution lifecycle | High |
| 8 | Project collaboration and reviewer comments | Share a project safely, assign tasks, review evidence and approve deliverables | Owner-scoped projects, tasks, assets and reports; membership/RBAC are additional work | High |
| 9 | Scheduled literature/dataset refresh | Re-run a saved workflow when new sources/data arrive and show a cited difference report | Existing graphs, paper discovery, assets; scheduler policy remains incomplete | High |

Prefer saved evidence, real comparisons and reproducibility first: they build directly on AIKDAP's strongest capabilities and make the output easier to trust and reuse. Collaboration and recurring work should follow storage, authorization, queue and idempotency improvements.

The array/table execution catalog is not fully runnable today: `backend/execution_launcher/docker_policy.py:143` and `:144` still use test-only image digests. Build and validate the required dispatcher images, retain network/resource restrictions, and supply a deployable execution service. The Render blueprint does not configure Docker-daemon access for the worker; expression execution being present locally does not establish hosted execution availability.

## Engineering and deployment hygiene

- Add backend/frontend `.dockerignore` files. Both Dockerfiles use `COPY . .`; local virtual environments, uploads, dependencies, scratch data and test outputs can enter the build context. `.gitignore` does not exclude files from Docker builds.
- Use `npm ci` for reproducible frontend builds. The root build and frontend Dockerfile currently use `npm install`. Split Python runtime and development requirements so lint/test/watch packages do not ship unnecessarily.
- Use a production Compose profile without reload or source bind mounts. The existing Compose configuration is suitable for local development. Run services under an appropriate non-root user where possible.
- Add a migration deployment step and CI that builds the frontend, checks lint, runs isolated unit tests, runs integration tests against disposable Postgres/Redis, and verifies generated API types. No `.github/workflows` directory was found.
- Improve the test environment: isolated settings and provider-health state for unit tests; disposable services for integration tests; Promise-correct download mocks; deliberate canvas/scroll/navigation stubs or real-browser coverage.
- Add end-to-end checks for upload → search → citation view, two-paper comparison, analytics totals, report export, and supported expression execution.
- Fill the empty root README with setup/run/test/deploy instructions. Refresh stale worker documentation that still describes real embedding tasks as placeholders.
- Prefer focused internal extraction from the largest modules as they change. `planner/nodes.py`, `core/llm/gateway.py` and `workers/tasks.py` are roughly 54–64 kB each. Keep module boundaries and external contracts stable; file size alone does not justify a rewrite.
- Add operational measures to existing structured logging: queue age, time until searchable, p95 stage durations, provider failures, run failures, token usage, retrieval quality and export success. Separate process liveness, critical dependency readiness and optional model diagnostics; current `/health` intentionally always returns HTTP 200.

## Evaluation quality

The repository contains an evaluation harness and stored calibration results, which is a useful foundation. The 6 September 2026 result file reports 77 relevance rows, with calibrated precision/recall 0.8571 at the BGE threshold. Evidence-intelligence scenarios explicitly include synthetic authored cases. These are historical fixtures, not a current production-quality measurement.

Hosted reranking uses Jina with a provisional 0.3 relevance threshold. Re-evaluate with that provider and representative real documents. Track retrieval recall@k, citation correctness, claim entailment, contradiction accuracy and insufficiency decisions separately. Citation-ID validity alone is insufficient to establish that a passage supports a claim. Add adversarial document instructions, ambiguous questions, OCR damage and foreign-project references to the evaluation set.

Avoid publishing a single accuracy percentage that combines synthetic cases, real retrieval labels and live model behavior.

## Suggested execution sequence

1. **Trust and correctness:** real cross-paper inputs/IDs/persistence; embedding-space compatibility; correct totals/pagination; session invalidation; clean test exits.
2. **Deployment reliability:** shared durable storage, outbox/idempotency, startup and periodic recovery, production container hygiene and disposable integration tests.
3. **Measured speed:** retrieval query plans/index benchmarks, bulk chunk writes, CPU offload, queue isolation, chart bundle reduction, prompt/run budgets.
4. **Product depth:** evidence notebook/comparison matrix, reproducibility exports, data quality and reusable deliverables.
5. **Scale:** collaboration, recurring updates, large dataset execution and additional sandbox capabilities.

For your roster: Newton owns UI, lists/charts/evidence workspace; Nicolas owns storage, retrieval, ML workflows, APIs and workers; Diana owns reproducible environments, failing checks and dependency/runtime diagnosis; Reginald verifies source fidelity, authorization, contracts, recovery and measured improvement. This is an ownership proposal; no agents were assigned or spawned during this review.

## Verification record

- Frontend production build passed, including TypeScript compilation. Vite reported large chunks; measured sizes are listed above.
- Frontend JSON test report recorded **325 passing assertions, zero failing assertions**. The command nevertheless exited **1**, with an uncaught `AssetsLibrary` download error because a mocked action returned `undefined` and `.catch()` was called. jsdom also reports unsupported canvas/scroll/navigation APIs. Treat this run as an unclean check.
- Python `pip check` reported no broken installed requirements.
- Ruff reported **17 E701** findings in the reviewed application/launcher scope. These are style findings, not a security or correctness assessment.
- Backend test collection initially failed because the local invocation did not have the four required settings. Broader attempts with dummy database settings were stopped after waiting in database fixture setup. The final selected unit run completed: **222 passed, 10 database-dependent tests deselected**, with two warnings. It used dummy settings, a process-local provider-health registry and LiteLLM's local cost map; these adjustments isolated verification from external services and did not modify application code. The files covered chunking, relevance, embeddings, LLM resilience, analytics plans/execution, experiment safety, Docker policy, workflow events and settings.
- Full database, OCR, real-Docker and live-provider integration checks were not performed. No new live performance baseline was measured for retrieval, ingestion or model inference.

Check outputs are saved beside this report in `review-evidence-2026-10-13/`: frontend build log, frontend assertion JSON, frontend runtime-error log and final backend unit-test log.
