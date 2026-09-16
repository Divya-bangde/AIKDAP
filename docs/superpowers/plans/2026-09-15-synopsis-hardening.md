# Synopsis Hardening Implementation Plan (Milestone 10 step 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps step 4 (reports base + Synopsis) left before step 5 starts: a persisted, visible trace for every report run; idempotent generation plus a retry endpoint and button; route-level and database-backed tests; a verified full suite. (Item 6, the knowledge-graph update, is skipped at the user's direction.)

**Architecture:** Report runs write their trace into the existing `research_steps` table. `run_id` becomes nullable and a nullable `asset_id` is added, with a check constraint saying exactly one of the two is set. A new `ReportStepTracker` (a `NodeExecutionTracker`, injected under `tracking.TRACKER_CONFIG_KEY`) is driven by the same `tracking.instrument` wrapper the research graph uses. The report graph grows from 3 to 4 nodes (collect → retrieve → write → coverage), so retrieval gets its own step. `GET /reports/{asset_id}` returns the report asset plus its steps. The frontend dialog polls that endpoint and renders the steps with the same `ResearchPipeline` component research runs use. Idempotency is an atomic `UPDATE … WHERE processing_status='pending'` claim. Retry resets a failed report to `pending` and re-enqueues it.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async), Alembic, Celery, LangGraph, pytest + httpx.ASGITransport, React, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-13-persona-features-design.md`, section 4 ("reuse `agents/planner/tracking.instrument` for the same trace, timeline, and failure policy"). Also `CLAUDE.md` "Explainable AI" and "Definition of Done". Builds on `docs/superpowers/plans/2026-09-15-synopsis.md`.

## Global Constraints

- Scope is ONLY hardening items 1–5 plus the two user-approved additions: an `attempt` column with the dialog grouped by attempt, and the reconciler failing a stale report's running steps. No new features, nothing from step 5 (build plan). Item 6 (graphify) is skipped by the user: do not run graphify or commit `graphify-out/`.
- Report steps: `research_steps.asset_id` is `ON DELETE CASCADE`. The existing run-scoped query (`list_by_run`, `WHERE run_id = :run_id`) can never match a row whose `run_id` is NULL.
- Branch `feature/synopsis`. Never merge or push.
- Commits: each commit covers exactly one item (1–6). An item may span several commits (one per task). Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Alembic only for schema changes. Current head is `e1a2b3c4d5f6` (verified with `docker exec aikdap_backend alembic heads`).
- Backend runs in Docker: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend <cmd>` from Git Bash (code bind-mounted at `/app`). After changing `workers/tasks.py` or anything it imports, run `docker compose restart worker` from the repo root.
- Never print or log API keys. Every error persisted to the DB is scrubbed: `"Report generation failed (<ExceptionType>)."`, never raw exception text.
- After changing API schemas, regenerate the frontend types with `npm run generate:api` from `frontend/` (backend on :8001).
- Route tests mock the Celery enqueue. No live LLM or embedding calls in any new test.
- Known environmental backend failures, ignored only if nothing else fails: `test_execution_*` (no Docker inside the container), `test_cross_paper`, `test_fallback_grounding` (live LLM providers). Known load-flaky frontend test: `Landing.test.tsx` "tells the whole pipeline story" (passes alone).
- Step statuses reuse `ResearchStepStatus`: `running` → `completed` | `failed`, and `skipped` for nodes that never ran after a failure.

---

## File Structure

**Backend**
- Create `backend/alembic/versions/f2b3c4d5e6a7_research_steps_asset_trace.py`: nullable `run_id`, new `asset_id` FK, exactly-one check constraint.
- Modify `backend/app/modules/research/models.py`: `ResearchStep.asset_id`, nullable `run_id`, check constraint.
- Modify `backend/app/modules/research/repository.py`: `ResearchStepRepository.list_by_asset`.
- Modify `backend/app/modules/research/schemas.py`: `ResearchStepRead.run_id` becomes optional, add `asset_id`.
- Modify `backend/app/agents/reports/state.py`: `ReportNode.RETRIEVE_EVIDENCE`, `ReportState.evidence`.
- Modify `backend/app/agents/reports/nodes.py`: split out `retrieve_evidence_node`; every node reports `step` summary/output; lister and searcher accept `str | UUID`; searcher takes an optional injected `KnowledgeBaseService`.
- Modify `backend/app/agents/reports/registry.py` and `graph.py`: 4-node linear graph.
- Create `backend/app/modules/reports/tracking.py`: `ReportStepTracker`, `scrub_report_error`.
- Modify `backend/app/workers/tasks.py`: tracker wiring, atomic claim; `_scrub_report_error` replaced by `scrub_report_error`.
- Modify `backend/app/modules/reports/repository.py`: `claim_pending`.
- Modify `backend/app/modules/reports/service.py`: `get_report`, `retry_report`, `ReportNotRetryableError`.
- Modify `backend/app/modules/reports/schemas.py`: `ReportRead`.
- Modify `backend/app/modules/reports/router.py`: `GET /reports/{asset_id}`, `POST /reports/{asset_id}/retry`.
- Modify `backend/tests/conftest.py`: `make_report_asset` fixture.
- Tests: create `tests/test_report_steps.py`, `tests/test_reports_routes.py`, `tests/test_report_dependencies.py`; extend `tests/test_reports_graph.py` and `tests/test_reports_service.py`.

**Frontend**
- Modify `frontend/src/types/api.d.ts` (regenerated).
- Modify `frontend/src/services/reports.ts`: `getReport`, `retryReport`.
- Modify `frontend/src/features/research/research-presentation.ts`: plain-language copy for the 4 report nodes.
- Modify `frontend/src/features/reports/GenerateSynopsisDialog.tsx`: poll `getReport`, render `ResearchPipeline`, Retry.
- Modify `frontend/src/features/assets/DocumentCard.tsx`: Retry on a failed generated report.
- Modify `frontend/src/test/fixtures.ts`: `makeReport`, `makeStep`.
- Tests: `GenerateSynopsisDialog.test.tsx` (rewritten), `DocumentCard.test.tsx` (extended), `services/reports.test.ts` (extended).

---

## Item 1 — Persisted report trace

### Task 1: `research_steps` can belong to a report asset

**Files:**
- Create: `backend/alembic/versions/f2b3c4d5e6a7_research_steps_asset_trace.py`
- Modify: `backend/app/modules/research/models.py` (class `ResearchStep`)
- Modify: `backend/app/modules/research/repository.py` (class `ResearchStepRepository`)
- Modify: `backend/app/modules/research/schemas.py` (class `ResearchStepRead`)
- Modify: `backend/tests/conftest.py` (add fixture)
- Test: `backend/tests/test_report_steps.py`

**Interfaces:**
- Produces: `ResearchStep.asset_id: Mapped[uuid.UUID | None]` (FK `assets.id`, `ON DELETE CASCADE`), `ResearchStep.run_id: Mapped[uuid.UUID | None]`, `ResearchStep.attempt: Mapped[int]` (NOT NULL, default 1); `ResearchStepRepository.list_by_asset(asset_id: uuid.UUID) -> list[ResearchStep]` (ordered by `attempt`, then `step_index`); `ResearchStepRepository.latest_attempt(asset_id: uuid.UUID) -> int` (0 when the asset has no steps); `ResearchStepRead.run_id: uuid.UUID | None`, `ResearchStepRead.asset_id: uuid.UUID | None = None`, `ResearchStepRead.attempt: int = 1`; pytest fixture `make_report_asset(project, *, status=AssetProcessingStatus.PENDING, kind="study_summary", processing_error=None, sections=None) -> Asset` (async factory; the asset is committed).

- [ ] **Step 1: Add the `make_report_asset` fixture to `backend/tests/conftest.py`**

Add these imports after the existing `from app.modules.projects.models import ...` line:

```python
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
```

Append at the end of the file:

```python
@pytest.fixture
def make_report_asset(session):
    """Factory for a committed GENERATED report asset (Milestone 10
    step 4), in the exact shape `ReportService.generate_synopsis`
    creates. Removed with its owner by `project`'s user cascade."""

    async def _make(
        project: Project,
        *,
        status: AssetProcessingStatus = AssetProcessingStatus.PENDING,
        kind: str = "study_summary",
        processing_error: str | None = None,
        sections: list[dict] | None = None,
    ) -> Asset:
        asset = Asset(
            project_id=project.id,
            owner_id=project.owner_id,
            title="Study Summary" if kind == "study_summary" else "Project Synopsis",
            description=None,
            asset_type=AssetType.SUMMARY if kind == "study_summary" else AssetType.REPORT,
            status=AssetStatus.ACTIVE,
            mime_type="application/json",
            file_name="study-summary.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            asset_metadata={"kind": kind, "sections": sections or []},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=project.owner_id,
            processing_status=status,
            processing_error=processing_error,
        )
        session.add(asset)
        await session.commit()
        return asset

    return _make
```

- [ ] **Step 2: Write the failing test `backend/tests/test_report_steps.py`**

```python
"""`research_steps` rows can belong to a report asset instead of a
research run (hardening item 1): exactly one owner, listed in order."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.research.enums import ResearchStepStatus
from app.modules.research.models import ResearchStep
from app.modules.research.repository import ResearchStepRepository
from app.modules.research.schemas import ResearchStepRead


def _step(asset_id, index: int, *, run_id=None, attempt: int | None = None) -> ResearchStep:
    extra = {} if attempt is None else {"attempt": attempt}
    return ResearchStep(
        run_id=run_id,
        asset_id=asset_id,
        step_index=index,
        **extra,
        node_name="collect_documents",
        title="Collect project documents",
        status=ResearchStepStatus.COMPLETED,
        summary="Collected 1 processed document(s).",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_ms=5,
    )


@pytest.mark.asyncio
async def test_a_report_asset_owns_its_steps_ordered_by_attempt_then_index(session, project, make_report_asset):
    report = await make_report_asset(project)
    repository = ResearchStepRepository(session)
    assert await repository.latest_attempt(report.id) == 0

    await repository.create(_step(report.id, 1, attempt=2))
    await repository.create(_step(report.id, 1))
    await repository.create(_step(report.id, 0, attempt=2))
    await repository.create(_step(report.id, 0))
    await session.commit()

    steps = await repository.list_by_asset(report.id)

    assert [(step.attempt, step.step_index) for step in steps] == [(1, 0), (1, 1), (2, 0), (2, 1)]
    assert await repository.latest_attempt(report.id) == 2
    read = ResearchStepRead.model_validate(steps[0])
    assert read.asset_id == report.id
    assert read.run_id is None
    assert read.attempt == 1


@pytest.mark.asyncio
async def test_a_step_with_no_owner_is_rejected(session, project):
    session.add(_step(None, 0))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_deleting_a_report_asset_cascades_to_its_steps(session, project, make_report_asset):
    from sqlalchemy import delete, func, select

    from app.modules.assets.models import Asset

    report = await make_report_asset(project)
    await ResearchStepRepository(session).create(_step(report.id, 0))
    await session.commit()

    # A Core DELETE, so it's the database's ON DELETE CASCADE doing the
    # work -- not an ORM-side cascade.
    await session.execute(delete(Asset).where(Asset.id == report.id))
    await session.commit()

    remaining = await session.scalar(
        select(func.count()).select_from(ResearchStep).where(ResearchStep.asset_id == report.id)
    )
    assert remaining == 0
```

- [ ] **Step 3: Run it and watch it fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_report_steps.py`
Expected: FAIL. `ResearchStep` has no `asset_id` (TypeError: 'asset_id' is an invalid keyword argument).

- [ ] **Step 4: Write the migration `backend/alembic/versions/f2b3c4d5e6a7_research_steps_asset_trace.py`**

```python
"""research steps can trace a report asset

Hardening item 1 for Milestone 10 step 4: report runs persist their
trace into the existing `research_steps` table instead of a second
step table (spec section 4: reports reuse `tracking.instrument` "for the
same trace, timeline, and failure policy"). A step now belongs to
exactly one owner -- a research run (`run_id`) or a report asset
(`asset_id`) -- enforced by a check constraint so a step can never be
orphaned or double-owned.

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-09-15 12:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_steps",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_research_steps_asset_id_assets"),
        "research_steps",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_research_steps_asset_id"), "research_steps", ["asset_id"])
    # Which generation attempt of a report a step belongs to (a retry is
    # attempt 2, ...). Existing research-run steps get 1.
    op.add_column(
        "research_steps",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column(
        "research_steps", "run_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
    )
    op.create_check_constraint(
        op.f("ck_research_steps_exactly_one_owner"),
        "research_steps",
        "num_nonnulls(run_id, asset_id) = 1",
    )


def downgrade() -> None:
    # Report-owned steps cannot survive `run_id` becoming NOT NULL again.
    op.execute("DELETE FROM research_steps WHERE run_id IS NULL")
    op.drop_constraint(op.f("ck_research_steps_exactly_one_owner"), "research_steps", type_="check")
    op.alter_column(
        "research_steps", "run_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )
    op.drop_column("research_steps", "attempt")
    op.drop_index(op.f("ix_research_steps_asset_id"), table_name="research_steps")
    op.drop_constraint(op.f("fk_research_steps_asset_id_assets"), "research_steps", type_="foreignkey")
    op.drop_column("research_steps", "asset_id")
```

- [ ] **Step 5: Update the model in `backend/app/modules/research/models.py`**

Change the sqlalchemy import line to add `CheckConstraint`:

```python
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
```

Replace the start of `class ResearchStep` (docstring through `run_id`) with:

```python
class ResearchStep(BaseModel):
    """One graph node's execution, within a research run OR a report run.

    A report run (Milestone 10 step 4) has no `research_runs` row -- its
    unit of work is the GENERATED report `Asset` -- so a step belongs to
    exactly one of `run_id` / `asset_id`, never both, never neither.
    Reusing this table rather than adding a second step table is what
    lets reports share the research trace's timeline and failure policy
    (spec section 4).
    """

    __tablename__ = "research_steps"
    __table_args__ = (
        CheckConstraint("num_nonnulls(run_id, asset_id) = 1", name="exactly_one_owner"),
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Which generation attempt of a report this step belongs to: 1 for
    #: the first run, +1 per retry. Always 1 for a research run's step.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
```

(Leave `step_index` and everything after it unchanged.)

- [ ] **Step 6: Add `list_by_asset` to `ResearchStepRepository` in `backend/app/modules/research/repository.py`**

Insert directly after `list_by_run`:

```python
    async def list_by_asset(self, asset_id: uuid.UUID) -> list[ResearchStep]:
        """List a report asset's steps across every generation attempt,
        attempt by attempt, each in execution order (a retry adds a new
        attempt; it never rewrites an earlier one)."""
        stmt = (
            select(ResearchStep)
            .where(ResearchStep.asset_id == asset_id)
            .order_by(ResearchStep.attempt, ResearchStep.step_index)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_attempt(self, asset_id: uuid.UUID) -> int:
        """The highest attempt number recorded for a report asset, or 0
        if it has no steps yet."""
        stmt = select(func.coalesce(func.max(ResearchStep.attempt), 0)).where(
            ResearchStep.asset_id == asset_id
        )
        return int(await self._session.scalar(stmt))
```

(Match `list_by_run`'s return expression exactly if it differs from `list(result.scalars().all())`. Add `func` to the file's `from sqlalchemy import ...` line if it isn't there.)

- [ ] **Step 7: Update `ResearchStepRead` in `backend/app/modules/research/schemas.py`**

Replace the line `    run_id: uuid.UUID` inside `class ResearchStepRead` with:

```python
    #: Set for a research run's step; null for a report run's step.
    run_id: uuid.UUID | None
    #: Set for a report run's step (Milestone 10 step 4); null otherwise.
    asset_id: uuid.UUID | None = None
    #: A report's generation attempt (1, then +1 per retry); 1 for research.
    attempt: int = 1
```

- [ ] **Step 8: Apply the migration and run the tests**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend alembic upgrade head`
Expected: `Running upgrade e1a2b3c4d5f6 -> f2b3c4d5e6a7`.
Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_report_steps.py tests/test_research_service.py`
Expected: all pass. (If `tests/test_research_service.py` does not exist, run `-k "research and step"` instead, to confirm research steps are unaffected.)

- [ ] **Step 9: Commit**

```bash
git add backend/alembic/versions/f2b3c4d5e6a7_research_steps_asset_trace.py backend/app/modules/research/models.py backend/app/modules/research/repository.py backend/app/modules/research/schemas.py backend/tests/conftest.py backend/tests/test_report_steps.py
git commit -m "feat(reports): let research_steps trace a report asset

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: A 4-node report graph where every node reports its step

**Files:**
- Modify: `backend/app/agents/reports/state.py`
- Modify: `backend/app/agents/reports/nodes.py`
- Modify: `backend/app/agents/reports/registry.py`
- Modify: `backend/app/agents/reports/graph.py`
- Test: `backend/tests/test_reports_graph.py` (extend)

**Interfaces:**
- Consumes: `app.agents.planner.tracking.NodeExecutionTracker`, `TRACKER_CONFIG_KEY`.
- Produces: `ReportNode.RETRIEVE_EVIDENCE = "retrieve_evidence"`; node names in order: `collect_documents`, `retrieve_evidence`, `write_sections`, `coverage_check`; `ReportState["evidence"]: dict[str, list[SectionEvidence]]` keyed by section title; each node's update carries `"step": {"summary": str, "output": dict}` (the tracker reads it). Registry titles: "Collect project documents", "Retrieve section evidence", "Write report sections", "Check coverage".

- [ ] **Step 1: Write the failing tests (append to `backend/tests/test_reports_graph.py`)**

First change `_run_graph` so it accepts a tracker (replace the whole function):

```python
async def _run_graph(*, kind: str, gateway, searcher, documents=None, tracker=None):
    from app.agents.planner.tracking import TRACKER_CONFIG_KEY

    dependencies = ReportGraphDependencies(
        document_lister=FakeDocumentLister(documents if documents is not None else _DOCS),
        section_searcher=searcher,
        llm_gateway=gateway,
    )
    configurable = {"dependencies": dependencies}
    if tracker is not None:
        configurable[TRACKER_CONFIG_KEY] = tracker
    graph = get_report_graph()
    return await graph.ainvoke(
        {"report_id": "r1", "project_id": "p1", "owner_id": "u1", "kind": kind},
        config={"configurable": configurable},
    )
```

Then append:

```python
from app.agents.planner.tracking import NodeExecutionTracker


class RecordingTracker(NodeExecutionTracker):
    def __init__(self) -> None:
        self.started: list[str] = []
        self.succeeded: dict[str, dict] = {}
        self.failed: list[str] = []

    async def on_node_start(self, node):
        self.started.append(node)

    async def on_node_success(self, node, update, duration_ms):
        self.succeeded[node] = update.get("step") or {}

    async def on_node_failure(self, node, error, duration_ms, critical):
        self.failed.append(node)


_REPORT_NODES = ["collect_documents", "retrieve_evidence", "write_sections", "coverage_check"]


@pytest.mark.asyncio
async def test_every_report_node_runs_in_order_and_reports_a_step_summary():
    from app.agents.reports.state import SECTION_QUERIES

    evidence = {query: [{"asset_id": "a1", "title": "Poultry Disease Paper", "file_name": "poultry.pdf", "snippet": "It found X."}] for query in SECTION_QUERIES.values()}
    tracker = RecordingTracker()

    await _run_graph(
        kind="study_summary",
        gateway=FakeGateway(_section_response("Section text.", ["a1"])),
        searcher=FakeSectionSearcher(evidence),
        tracker=tracker,
    )

    assert tracker.started == _REPORT_NODES
    assert list(tracker.succeeded) == _REPORT_NODES
    for node in _REPORT_NODES:
        assert tracker.succeeded[node]["summary"], node
    assert tracker.succeeded["collect_documents"]["output"] == {"document_count": 1}
    assert tracker.succeeded["write_sections"]["output"]["covered"] == 3


@pytest.mark.asyncio
async def test_a_retrieval_failure_is_recorded_against_retrieve_evidence_and_stops_the_run():
    class _FailingSearcher:
        async def search(self, **kwargs):
            raise RuntimeError("search backend down")

    tracker = RecordingTracker()
    gateway = FakeGateway(_section_response("unused", []))

    with pytest.raises(RuntimeError):
        await _run_graph(kind="study_summary", gateway=gateway, searcher=_FailingSearcher(), tracker=tracker)

    assert tracker.started == ["collect_documents", "retrieve_evidence"]
    assert tracker.failed == ["retrieve_evidence"]
    assert gateway.calls == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_graph.py`
Expected: the two new tests FAIL (`started` is missing `retrieve_evidence`; there are no step summaries). The existing tests still pass.

- [ ] **Step 3: Update `backend/app/agents/reports/state.py`**

In `class ReportNode`, add between `COLLECT_DOCUMENTS` and `WRITE_SECTIONS`:

```python
    RETRIEVE_EVIDENCE = "retrieve_evidence"
```

In `class ReportState`, add after the `documents` block:

```python
    # --- retrieve_evidence output: excerpts per section title (never
    # --- keyed for "References", which is built, not retrieved) ---
    evidence: dict[str, list[SectionEvidence]]
```

- [ ] **Step 4: Update the nodes in `backend/app/agents/reports/nodes.py`**

Replace `collect_documents_node` and `write_sections_node` (everything from `async def collect_documents_node` down to just before `async def coverage_check_node`) with:

```python
async def collect_documents_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Collect every processed document's AI-profile summary and topics."""
    dependencies = _dependencies(config)
    documents = await dependencies.document_lister.list_processed(state["project_id"])
    logger.info("report_documents_collected", report_id=state.get("report_id"), document_count=len(documents))
    return {
        "documents": documents,
        "step": {
            "summary": f"Collected {len(documents)} processed document(s).",
            "output": {"document_count": len(documents)},
        },
    }


async def retrieve_evidence_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Retrieve evidence for every section except References.

    Its own node, so retrieval has its own step in the trace (timing,
    failure) separate from the LLM writing that follows."""
    dependencies = _dependencies(config)
    documents = state.get("documents", [])
    document_lookup = {document["asset_id"]: document for document in documents}
    evidence_by_title: dict[str, list[SectionEvidence]] = {}

    for title in SECTION_TITLES[state["kind"]]:
        if title == "References":
            continue
        evidence = await dependencies.section_searcher.search(
            owner_id=state["owner_id"],
            project_id=state["project_id"],
            query=SECTION_QUERIES.get(title, title),
            limit=SECTION_EVIDENCE_LIMIT,
        )
        # Searchers don't have the project's document list, so fill in
        # the real title/file_name from the collected documents -- the
        # model then sees `[id] <real title> (<real file name>):`.
        evidence_by_title[title] = [
            {**item, "title": document_lookup[item["asset_id"]]["title"], "file_name": document_lookup[item["asset_id"]]["file_name"]}
            if item["asset_id"] in document_lookup
            else item
            for item in evidence
        ]

    with_evidence = sum(1 for items in evidence_by_title.values() if items)
    excerpt_count = sum(len(items) for items in evidence_by_title.values())
    logger.info(
        "report_evidence_retrieved",
        report_id=state.get("report_id"),
        sections_with_evidence=with_evidence,
        section_count=len(evidence_by_title),
    )
    return {
        "evidence": evidence_by_title,
        "step": {
            "summary": f"Found evidence for {with_evidence} of {len(evidence_by_title)} section(s).",
            "output": {
                "section_count": len(evidence_by_title),
                "sections_with_evidence": with_evidence,
                "excerpt_count": excerpt_count,
            },
        },
    }


async def write_sections_node(state: ReportState, config: RunnableConfig) -> dict[str, Any]:
    """Write each section with one LLM call, from the evidence
    `retrieve_evidence_node` found -- or mark it uncovered without ever
    calling the model when there is none (spec section 7)."""
    dependencies = _dependencies(config)
    kind = state["kind"]
    documents = state.get("documents", [])
    evidence_by_title = state.get("evidence", {})
    results: list[SectionResult] = []
    llm_calls = 0

    for title in SECTION_TITLES[kind]:
        if title == "References":
            # Built deterministically in `coverage_check_node` from what
            # the other sections actually cited -- never retrieved or
            # sent to the model (spec section 7: never invent citations).
            results.append(SectionResult(title=title, content="", covered=False, citations=[]))
            continue

        evidence = evidence_by_title.get(title, [])
        if not evidence:
            results.append(
                SectionResult(title=title, content="Not covered by your documents.", covered=False, citations=[])
            )
            continue

        prompt = render_section_prompt(title=title, kind=kind, evidence=evidence, documents=documents)
        response = await dependencies.llm_gateway.generate(
            prompt=prompt,
            system_prompt=SECTION_SYSTEM_PROMPT,
            model=settings.synthesis_model,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "SectionDraft", "schema": SectionDraft.model_json_schema()},
            },
        )
        llm_calls += 1
        draft = _parse_section(response.content, title)

        valid_asset_ids = {item["asset_id"] for item in evidence}
        citations = [asset_id for asset_id in draft.cited_asset_ids if asset_id in valid_asset_ids]
        content = draft.content.strip() or "Not covered by your documents."
        covered = content != "Not covered by your documents."
        results.append(
            SectionResult(
                title=title,
                content=content,
                covered=covered,
                # A section marked not-covered must never carry
                # citations forward into References -- an empty draft
                # earned no evidence credit even if the model happened
                # to name asset ids before going empty.
                citations=citations if covered else [],
            )
        )

    written = [section for section in results if section["title"] != "References"]
    covered_count = sum(1 for section in written if section["covered"])
    logger.info(
        "report_sections_written",
        report_id=state.get("report_id"),
        covered=covered_count,
        total=len(written),
    )
    return {
        "sections": results,
        "step": {
            "summary": (
                f"Wrote {covered_count} of {len(written)} section(s); "
                f"{len(written) - covered_count} not covered by your documents."
            ),
            "output": {"covered": covered_count, "total": len(written), "llm_calls": llm_calls},
        },
    }
```

In `coverage_check_node`, replace its final `logger.info(...)` and `return {"sections": sections}` with:

```python
    covered_count = sum(1 for section in sections if section["covered"])
    reference_count = len(
        next((section["citations"] for section in sections if section["title"] == "References"), [])
    )
    logger.info(
        "report_coverage_checked",
        report_id=state.get("report_id"),
        covered=covered_count,
        total=len(sections),
    )
    return {
        "sections": sections,
        "step": {
            "summary": f"{covered_count} of {len(sections)} section(s) covered; {reference_count} document(s) referenced.",
            "output": {"covered": covered_count, "total": len(sections), "reference_count": reference_count},
        },
    }
```

- [ ] **Step 5: Register the new node in `backend/app/agents/reports/registry.py`**

Change the nodes import to:

```python
from app.agents.reports.nodes import (
    collect_documents_node,
    coverage_check_node,
    retrieve_evidence_node,
    write_sections_node,
)
```

Insert this entry between the `COLLECT_DOCUMENTS` and `WRITE_SECTIONS` entries:

```python
    ReportNode.RETRIEVE_EVIDENCE.value: NodeSpec(
        name=ReportNode.RETRIEVE_EVIDENCE.value,
        title="Retrieve section evidence",
        handler=retrieve_evidence_node,
        critical=True,
        description="Searches the project's documents for evidence for each section.",
    ),
```

Change the `WRITE_SECTIONS` description to `"Writes each section from its evidence, one LLM call per section."`.

- [ ] **Step 6: Wire the edges in `backend/app/agents/reports/graph.py`**

Replace the three `add_edge` lines between `START` and `END` with:

```python
    builder.add_edge(START, ReportNode.COLLECT_DOCUMENTS.value)
    builder.add_edge(ReportNode.COLLECT_DOCUMENTS.value, ReportNode.RETRIEVE_EVIDENCE.value)
    builder.add_edge(ReportNode.RETRIEVE_EVIDENCE.value, ReportNode.WRITE_SECTIONS.value)
    builder.add_edge(ReportNode.WRITE_SECTIONS.value, ReportNode.COVERAGE_CHECK.value)
    builder.add_edge(ReportNode.COVERAGE_CHECK.value, END)
```

Update the module docstring's "collect documents, write sections, check coverage" to "collect documents, retrieve evidence, write sections, check coverage".

- [ ] **Step 7: Run the graph tests**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_graph.py tests/test_reports_service.py`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/agents/reports/state.py backend/app/agents/reports/nodes.py backend/app/agents/reports/registry.py backend/app/agents/reports/graph.py backend/tests/test_reports_graph.py
git commit -m "feat(reports): split retrieval into its own node and report every node's step

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Persist the trace from the Celery task

**Files:**
- Create: `backend/app/modules/reports/tracking.py`
- Modify: `backend/app/workers/tasks.py` (report section + imports)
- Test: `backend/tests/test_reports_service.py` (extend)

**Interfaces:**
- Consumes: `ResearchStepRepository.list_by_asset`, `ResearchStep.asset_id` (Task 1); node names and `step` payloads (Task 2).
- Produces: `app.modules.reports.tracking.scrub_report_error(exc: BaseException) -> str` returning `f"Report generation failed ({type(exc).__name__})."`; `ReportStepTracker(session: AsyncSession, asset_id: uuid.UUID, *, attempt: int)` (every step it writes carries that `attempt`, with `step_index` starting at 0); `_generate_report` runs attempt `latest_attempt(asset_id) + 1`; with `on_node_start`/`on_node_success`/`on_node_failure` and `async record_skipped() -> None`; `SKIPPED_SUMMARY = "Not run: an earlier step failed."`. `tasks._scrub_report_error` is removed.

- [ ] **Step 1: Write the failing tests (append to `backend/tests/test_reports_service.py`)**

```python
class _OneDocumentLister:
    async def list_processed(self, project_id):
        return [{"asset_id": "a1", "title": "t", "file_name": "f.pdf", "summary": "s", "topics": []}]


class _AllEvidenceSearcher:
    async def search(self, *, owner_id, project_id, query, limit):
        return [{"asset_id": "a1", "title": "t", "file_name": "f.pdf", "snippet": "evidence"}]


class _Gateway:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls = 0

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return LLMResponse(
            content='{"content": "Section text.", "cited_asset_ids": ["a1"]}',
            model="fake-model",
            provider="fake",
            latency_ms=5,
        )


def _patch_dependencies(monkeypatch, gateway) -> None:
    from app.agents.reports.nodes import ReportGraphDependencies

    monkeypatch.setattr(
        "app.workers.tasks.build_report_dependencies",
        lambda worker_session: ReportGraphDependencies(
            document_lister=_OneDocumentLister(),
            section_searcher=_AllEvidenceSearcher(),
            llm_gateway=gateway,
        ),
    )


async def _steps_for(asset_id):
    from app.database.session import async_session_factory
    from app.modules.research.repository import ResearchStepRepository

    async with async_session_factory() as verify_session:
        return await ResearchStepRepository(verify_session).list_by_asset(asset_id)


@pytest.mark.asyncio
async def test_a_completed_report_run_persists_one_completed_step_per_node(project, monkeypatch, make_report_asset):
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    _patch_dependencies(monkeypatch, _Gateway())

    await _generate_report(report.id)

    steps = await _steps_for(report.id)
    assert [step.node_name for step in steps] == ["collect_documents", "retrieve_evidence", "write_sections", "coverage_check"]
    assert [step.step_index for step in steps] == [0, 1, 2, 3]
    assert {step.attempt for step in steps} == {1}
    for step in steps:
        assert step.status.value == "completed"
        assert step.summary
        assert step.duration_ms is not None
        assert step.error_message is None
        assert step.run_id is None


@pytest.mark.asyncio
async def test_a_failed_node_marks_its_step_failed_scrubbed_and_skips_the_rest(project, monkeypatch, make_report_asset):
    from app.database.session import async_session_factory
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    _patch_dependencies(monkeypatch, _Gateway(raises=RuntimeError("https://secret.example/?key=super-secret")))

    await _generate_report(report.id)

    steps = await _steps_for(report.id)
    assert [(step.node_name, step.status.value) for step in steps] == [
        ("collect_documents", "completed"),
        ("retrieve_evidence", "completed"),
        ("write_sections", "failed"),
        ("coverage_check", "skipped"),
    ]
    failed = steps[2]
    assert failed.error_message == "Report generation failed (RuntimeError)."
    assert steps[3].summary == "Not run: an earlier step failed."

    async with async_session_factory() as verify_session:
        refreshed = await AssetRepository(verify_session).get_by_id(report.id)
    assert refreshed.processing_status.value == "failed"
    assert refreshed.asset_metadata["sections"] == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_service.py -k "persists_one_completed_step or failed_node_marks"`
Expected: FAIL. `_steps_for` returns `[]` because no tracker is wired.

- [ ] **Step 3: Create `backend/app/modules/reports/tracking.py`**

```python
"""Persists a report run's trace (hardening item 1, spec section 4).

The database-backed `NodeExecutionTracker` for the report graph: the
same contract `agents.planner.tracking.instrument` drives for research
runs, writing into the same `research_steps` table -- keyed by the
report `Asset` (`asset_id`) instead of a research run, since a report
has no `research_runs` row.

Unlike `research.service.ResearchStepTracker` it writes no
`agent_messages`: those rows require a `run_id`, and a report node's
whole observable output (summary, counts, timing, error) already lives
on the step itself.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner.tracking import NodeExecutionTracker
from app.agents.reports.registry import REPORT_AGENT_REGISTRY, get_node_spec
from app.modules.research.enums import ResearchStepStatus
from app.modules.research.models import ResearchStep
from app.modules.research.repository import ResearchStepRepository

#: Summary recorded on every node that never ran because an earlier,
#: critical node failed -- the trace shows the whole pipeline, not only
#: the nodes that happened to start.
SKIPPED_SUMMARY = "Not run: an earlier step failed."


def scrub_report_error(exc: BaseException) -> str:
    """The only error text ever persisted for a report -- on the asset
    and on its failed step. Keeps just the exception type name: never
    sensitive, and enough to tell failure modes apart. Matches
    `paper_suggestion.OpenAlexProvider`'s scrubbing discipline."""
    return f"Report generation failed ({type(exc).__name__})."


class ReportStepTracker(NodeExecutionTracker):
    """Writes one `research_steps` row per report node, `running ->
    completed | failed`, committing per node so a later failure never
    erases completed steps.

    Every row carries this run's `attempt` (1, then +1 per retry), so a
    retried report keeps each attempt's trace separately and in order."""

    def __init__(self, session: AsyncSession, asset_id: uuid.UUID, *, attempt: int) -> None:
        self._session = session
        self._asset_id = asset_id
        self._attempt = attempt
        self._steps = ResearchStepRepository(session)
        self._step_index = 0
        # A plain UUID, not an ORM instance: the failure path's rollback
        # expires every loaded object (same reasoning as
        # `research.service.ResearchStepTracker`).
        self._current_step_id: uuid.UUID | None = None
        self.started_nodes: list[str] = []

    async def on_node_start(self, node: str) -> None:
        step = await self._steps.create(
            ResearchStep(
                asset_id=self._asset_id,
                attempt=self._attempt,
                step_index=self._next_index(),
                node_name=node,
                title=get_node_spec(node).title,
                status=ResearchStepStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
        )
        self._current_step_id = step.id
        self.started_nodes.append(node)
        await self._session.commit()

    async def on_node_success(self, node: str, update: dict[str, Any], duration_ms: int) -> None:
        step = await self._current_step()
        if step is None:
            return
        report = update.get("step") or {}
        step.status = ResearchStepStatus.COMPLETED
        step.summary = report.get("summary")
        step.output_payload = report.get("output")
        step.completed_at = datetime.now(timezone.utc)
        step.duration_ms = duration_ms
        await self._session.commit()

    async def on_node_failure(
        self, node: str, error: BaseException, duration_ms: int, critical: bool
    ) -> None:
        # The error may have poisoned the transaction; the `running` row
        # was already committed, so rolling back loses nothing.
        await self._session.rollback()
        step = await self._current_step()
        if step is None:
            return
        step.status = ResearchStepStatus.FAILED
        step.summary = f"Failed: {get_node_spec(node).title}."
        step.error_message = scrub_report_error(error)
        step.completed_at = datetime.now(timezone.utc)
        step.duration_ms = duration_ms
        await self._session.commit()

    async def record_skipped(self) -> None:
        """Record every registered node that never started as `skipped`."""
        for name, spec in REPORT_AGENT_REGISTRY.items():
            if name in self.started_nodes:
                continue
            await self._steps.create(
                ResearchStep(
                    asset_id=self._asset_id,
                    attempt=self._attempt,
                    step_index=self._next_index(),
                    node_name=name,
                    title=spec.title,
                    status=ResearchStepStatus.SKIPPED,
                    summary=SKIPPED_SUMMARY,
                )
            )
        await self._session.commit()

    async def _current_step(self) -> ResearchStep | None:
        if self._current_step_id is None:
            return None
        return await self._session.get(ResearchStep, self._current_step_id)

    def _next_index(self) -> int:
        index = self._step_index
        self._step_index += 1
        return index
```

- [ ] **Step 4: Wire it into `backend/app/workers/tasks.py`**

Add to the imports (next to the existing `app.agents.reports` imports):

```python
from app.agents.planner.tracking import TRACKER_CONFIG_KEY
from app.modules.reports.tracking import ReportStepTracker, scrub_report_error
from app.modules.research.repository import ResearchStepRepository
```

(If `TRACKER_CONFIG_KEY` or `ResearchStepRepository` is already imported in `tasks.py`, don't import it twice.)

Delete the `_scrub_report_error` function and the comment block directly above it (the `#: Returned instead of the raw exception text ...` lines). Then replace the whole body of `_generate_report` with:

```python
async def _generate_report(asset_id: uuid.UUID) -> dict[str, str]:
    async with async_session_factory() as session:
        assets = AssetRepository(session)
        asset = await assets.get_by_id(asset_id)
        if asset is None:
            logger.error("report_generation_asset_missing", asset_id=str(asset_id))
            return {"status": "asset_missing", "asset_id": str(asset_id)}

        asset.processing_status = AssetProcessingStatus.RUNNING
        await session.commit()

        # Each run is its own attempt, numbered after any earlier ones,
        # so a retried report keeps every attempt's trace separately.
        attempt = await ResearchStepRepository(session).latest_attempt(asset_id) + 1
        tracker = ReportStepTracker(session, asset_id, attempt=attempt)

        try:
            dependencies = build_report_dependencies(session)
            graph = get_report_graph()
            result = await graph.ainvoke(
                {
                    "report_id": str(asset.id),
                    "project_id": str(asset.project_id),
                    "owner_id": str(asset.owner_id),
                    "kind": asset.asset_metadata.get("kind"),
                },
                config={"configurable": {"dependencies": dependencies, TRACKER_CONFIG_KEY: tracker}},
            )
            asset.asset_metadata = {**asset.asset_metadata, "sections": result["sections"]}
            asset.processing_status = AssetProcessingStatus.COMPLETED
            asset.processing_error = None
        except Exception as exc:
            logger.error(
                "report_generation_failed",
                asset_id=str(asset_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            # A DB error (e.g. from the KB searcher or document lister,
            # both sharing this session) leaves the transaction failed;
            # roll back before writing again, or the commit below raises
            # `PendingRollbackError` and the asset stays `running`.
            await session.rollback()
            await tracker.record_skipped()
            asset = await assets.get_by_id(asset_id)
            if asset is not None:
                # Sections are only ever written on success, so a failed
                # report never carries a partial document.
                asset.processing_status = AssetProcessingStatus.FAILED
                asset.processing_error = scrub_report_error(exc)
            await session.commit()
            return {"status": "ok", "asset_id": str(asset_id)}

        await session.commit()

    return {"status": "ok", "asset_id": str(asset_id)}
```

Search the backend for any remaining `_scrub_report_error` reference (`grep -rn _scrub_report_error backend/`) and point it at `scrub_report_error`.

- [ ] **Step 5: Run the tests, then restart the worker**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_service.py tests/test_reports_graph.py tests/test_report_steps.py`
Expected: all pass, including the existing LLM-failure and DB-error tests.
Run from the repo root: `docker compose restart worker`

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/reports/tracking.py backend/app/workers/tasks.py backend/tests/test_reports_service.py
git commit -m "feat(reports): persist a step trace for every report run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `GET /reports/{asset_id}` returns the report with its steps

**Files:**
- Modify: `backend/app/modules/reports/schemas.py`
- Modify: `backend/app/modules/reports/service.py`
- Modify: `backend/app/modules/reports/router.py`
- Test: `backend/tests/test_reports_service.py` (extend)

**Interfaces:**
- Consumes: `ResearchStepRepository.list_by_asset`, `ResearchStepRead` (Task 1).
- Produces: `ReportRead(AssetRead)` with `steps: list[ResearchStepRead]` and classmethod `from_report(asset: Asset, steps: list[ResearchStep]) -> ReportRead`; `ReportService.get_report(owner_id, asset_id) -> tuple[Asset, list[ResearchStep]]` (raises `ReportNotFoundError`); route `GET /api/v1/reports/{asset_id}` → 200 `ReportRead` | 404.

- [ ] **Step 1: Write the failing test (append to `backend/tests/test_reports_service.py`)**

```python
@pytest.mark.asyncio
async def test_get_report_returns_the_asset_with_its_steps_and_hides_it_from_others(session, project, make_report_asset):
    from datetime import datetime, timezone

    from app.modules.reports.schemas import ReportRead
    from app.modules.research.enums import ResearchStepStatus
    from app.modules.research.models import ResearchStep

    report = await make_report_asset(project, status=AssetProcessingStatus.COMPLETED)
    session.add(
        ResearchStep(
            asset_id=report.id,
            step_index=0,
            node_name="collect_documents",
            title="Collect project documents",
            status=ResearchStepStatus.COMPLETED,
            summary="Collected 1 processed document(s).",
            completed_at=datetime.now(timezone.utc),
            duration_ms=4,
        )
    )
    await session.commit()

    asset, steps = await ReportService(session).get_report(project.owner_id, report.id)
    read = ReportRead.from_report(asset, steps)

    assert read.id == report.id
    assert [step.node_name for step in read.steps] == ["collect_documents"]
    with pytest.raises(ReportNotFoundError):
        await ReportService(session).get_report(uuid.uuid4(), report.id)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_service.py -k get_report_returns`
Expected: FAIL with `ImportError: cannot import name 'ReportRead'`.

- [ ] **Step 3: Add `ReportRead` to `backend/app/modules/reports/schemas.py`**

Change the imports to:

```python
import enum
import uuid

from pydantic import BaseModel, Field

from app.modules.assets.enums import AssetProcessingStatus
from app.modules.assets.models import Asset
from app.modules.assets.schemas import AssetRead
from app.modules.research.models import ResearchStep
from app.modules.research.schemas import ResearchStepRead
```

Append:

```python
class ReportRead(AssetRead):
    """`GET /reports/{asset_id}`: the report asset plus its persisted
    step trace (hardening item 1), so the frontend renders a report run
    with the same pipeline view a research run uses. A separate report
    endpoint, rather than widening `AssetRead`, keeps the assets module
    unaware of the research step table."""

    steps: list[ResearchStepRead] = Field(default_factory=list)

    @classmethod
    def from_report(cls, asset: Asset, steps: list[ResearchStep]) -> "ReportRead":
        return cls(
            **AssetRead.from_model(asset).model_dump(),
            steps=[ResearchStepRead.model_validate(step) for step in steps],
        )
```

- [ ] **Step 4: Add `get_report` to `ReportService` in `backend/app/modules/reports/service.py`**

Add imports:

```python
from app.modules.research.models import ResearchStep
from app.modules.research.repository import ResearchStepRepository
```

In `__init__`, add `self._steps = ResearchStepRepository(session)`. Insert after `get_owned_report`:

```python
    async def get_report(
        self, owner_id: uuid.UUID, asset_id: uuid.UUID
    ) -> tuple[Asset, list[ResearchStep]]:
        """Fetch an owned report asset and its step trace, in order."""
        asset = await self.get_owned_report(owner_id, asset_id)
        steps = await self._steps.list_by_asset(asset.id)
        return asset, steps
```

- [ ] **Step 5: Add the route to `backend/app/modules/reports/router.py`**

Change the schemas import to `from app.modules.reports.schemas import ReportGenerateRequest, ReportGenerationAccepted, ReportRead`. Append:

```python
@router.get("/reports/{asset_id}", response_model=ReportRead)
async def get_report_route(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportRead:
    """A report's status, error, sections, and full step trace."""
    try:
        asset, steps = await service.get_report(current_user.id, asset_id)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    return ReportRead.from_report(asset, steps)
```

Also update `generate_synopsis_route`'s docstring to "poll `GET /reports/{asset_id}`".

- [ ] **Step 6: Run the tests and check the route is registered**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_service.py`
Expected: all pass.
Run: `curl -s http://localhost:8001/openapi.json | grep -o '"/api/v1/reports/{asset_id}"'`
Expected: prints the path.

- [ ] **Step 7: Commit**

```bash
git add backend/app/modules/reports/schemas.py backend/app/modules/reports/service.py backend/app/modules/reports/router.py backend/tests/test_reports_service.py
git commit -m "feat(reports): expose a report's step trace on GET /reports/{asset_id}

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — show the report trace

**Files:**
- Modify: `frontend/src/types/api.d.ts` (regenerated)
- Modify: `frontend/src/services/reports.ts`
- Modify: `frontend/src/services/reports.test.ts`
- Modify: `frontend/src/features/research/research-presentation.ts`
- Modify: `frontend/src/test/fixtures.ts`
- Modify: `frontend/src/features/reports/GenerateSynopsisDialog.tsx`
- Modify: `frontend/src/features/reports/GenerateSynopsisDialog.test.tsx` (rewritten)

**Interfaces:**
- Consumes: `GET /api/v1/reports/{asset_id}` → `components["schemas"]["ReportRead"]` (Task 4).
- Produces: `reportsService.getReport(assetId: string): Promise<ReportRead>`; fixtures `makeReport(overrides?: Partial<ReportRead>): ReportRead` and `makeStep(overrides?: Partial<ResearchStepRead>): ResearchStepRead`; dialog query key `["reports", "report", assetId]` (Task 7 invalidates it).

- [ ] **Step 1: Regenerate the API types**

Run (from `frontend/`): `npm run generate:api`
Expected: `src/types/api.d.ts` gains `ReportRead` (with `steps`) and the `/api/v1/reports/{asset_id}` path. `ResearchStepRead.run_id` becomes `string | null`, and `asset_id` (`string | null`) and `attempt` (`number`) appear. FastAPI's output schemas mark defaulted fields as required, so every hand-built `ResearchStepRead` object in the existing research tests (`ResearchPipeline.test.tsx`, `ResearchRunView.test.tsx`, `ResearchResult.test.tsx`, and any other hit from `grep -rn "step_index:" frontend/src --include=*.test.tsx`) must gain `asset_id: null, attempt: 1`. Do that in this step; `npx tsc -b` must be clean before Step 2.

- [ ] **Step 2: Add the fixtures to `frontend/src/test/fixtures.ts`**

Add to the type aliases at the top:

```ts
type ReportRead = components["schemas"]["ReportRead"];
type ResearchStepRead = components["schemas"]["ResearchStepRead"];
```

Append:

```ts
/** A GENERATED report asset (study summary) with its step trace. */
export function makeReport(overrides: Partial<ReportRead> = {}): ReportRead {
  return {
    ...makeAsset({
      title: "Study Summary",
      asset_type: "summary",
      source: "generated",
      mime_type: "application/json",
      file_name: "study-summary.json",
      file_extension: "json",
      file_size: 0,
    }),
    steps: [],
    ...overrides,
  };
}

/** One report-run step, completed unless overridden. */
export function makeStep(overrides: Partial<ResearchStepRead> = {}): ResearchStepRead {
  return {
    id: "s1",
    run_id: null,
    asset_id: "a1",
    attempt: 1,
    step_index: 0,
    node_name: "collect_documents",
    title: "Collect project documents",
    status: "completed",
    summary: "Collected 1 processed document(s).",
    output_payload: { document_count: 1 },
    error_message: null,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    duration_ms: 12,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}
```

- [ ] **Step 3: Write the failing service test (append inside the existing `describe` in `frontend/src/services/reports.test.ts`)**

Follow the file's existing mocking of `@/services/client` (`vi.mocked(client.request)`):

```ts
  it("fetches a report with its steps", async () => {
    vi.mocked(client.request).mockResolvedValue({ id: "a1", steps: [] });

    await reportsService.getReport("a1");

    expect(client.request).toHaveBeenCalledWith("/api/v1/reports/a1");
  });
```

(Match the existing test's import names for `client` and `reportsService`.)

- [ ] **Step 4: Add `getReport` to `frontend/src/services/reports.ts`**

Add the type alias `type ReportRead = components["schemas"]["ReportRead"];` and:

```ts
export function getReport(assetId: string) {
  return request<ReportRead>(`/api/v1/reports/${assetId}`);
}
```

- [ ] **Step 5: Add plain-language copy for the report nodes in `frontend/src/features/research/research-presentation.ts`**

Add these entries to `PRESENTATION` (after `synthesis`):

```ts
  // Report runs (Milestone 10 step 4) share this pipeline view; these
  // are the four real `node_name`s from `backend/app/agents/reports`.
  collect_documents: {
    title: "Gathering your documents",
    description: "Collecting every processed document in this project.",
  },
  retrieve_evidence: {
    title: "Finding evidence for each section",
    description: "Searching your documents for passages that support each section.",
  },
  write_sections: {
    title: "Writing the report",
    description: "Drafting each section only from that evidence — a section without evidence is marked as not covered.",
  },
  coverage_check: {
    title: "Checking coverage",
    description: "Building the references from the documents actually cited.",
  },
```

- [ ] **Step 6: Rewrite `frontend/src/features/reports/GenerateSynopsisDialog.test.tsx` (failing tests first)**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GenerateSynopsisDialog } from "@/features/reports/GenerateSynopsisDialog";
import { makeReport, makeStep } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import * as reportsService from "@/services/reports";

vi.mock("@/services/reports");

async function startGeneration(kindLabel = "Study summary") {
  const user = userEvent.setup();
  renderWithProviders(<GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />);
  await user.click(screen.getByText(kindLabel));
  await user.click(screen.getByRole("button", { name: /generate/i }));
  return user;
}

describe("GenerateSynopsisDialog", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lets the user choose a kind and starts generation", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(makeReport({ id: "a1", processing_status: "pending" }));

    await startGeneration();

    await waitFor(() =>
      expect(reportsService.generateSynopsis).toHaveBeenCalledWith("p1", "study_summary"),
    );
    expect(await screen.findByText(/generating your report/i)).toBeInTheDocument();
  });

  it("renders the report's pipeline steps as they arrive", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "a1",
        processing_status: "running",
        steps: [
          makeStep(),
          makeStep({ id: "s2", step_index: 1, node_name: "retrieve_evidence", title: "Retrieve section evidence", status: "running", completed_at: null, duration_ms: null }),
        ],
      }),
    );

    await startGeneration();

    expect(await screen.findByText("Gathering your documents")).toBeInTheDocument();
    expect(screen.getByText("Finding evidence for each section")).toBeInTheDocument();
  });

  it("groups the steps by attempt when a report was retried", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "a1",
        processing_status: "running",
        steps: [
          makeStep({ id: "s1", attempt: 1, step_index: 0 }),
          makeStep({ id: "s2", attempt: 1, step_index: 1, node_name: "write_sections", title: "Write report sections", status: "failed", error_message: "Report generation failed (RuntimeError)." }),
          makeStep({ id: "s3", attempt: 2, step_index: 0, status: "running", completed_at: null, duration_ms: null }),
        ],
      }),
    );

    await startGeneration();

    const first = await screen.findByRole("region", { name: "Attempt 1" });
    const second = screen.getByRole("region", { name: "Attempt 2" });
    expect(first).toHaveTextContent("Writing the report");
    expect(second).toHaveTextContent("Gathering your documents");
    expect(second).not.toHaveTextContent("Writing the report");
    expect(screen.getByText("Attempt 2 (latest)")).toBeInTheDocument();
  });

  it("shows no attempt headings for a report run only once", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "a1", processing_status: "running", steps: [makeStep()] }),
    );

    await startGeneration();

    expect(await screen.findByText("Gathering your documents")).toBeInTheDocument();
    expect(screen.queryByText(/^Attempt 1/)).not.toBeInTheDocument();
  });

  it("surfaces a failure with the backend's error message and the failed step", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({
        id: "a1",
        processing_status: "failed",
        processing_error: "Report generation failed (RuntimeError).",
        steps: [
          makeStep(),
          makeStep({ id: "s2", step_index: 1, node_name: "write_sections", title: "Write report sections", status: "failed", error_message: "Report generation failed (RuntimeError)." }),
        ],
      }),
    );

    await startGeneration("Project synopsis");

    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((alert) => /report generation failed/i.test(alert.textContent ?? ""))).toBe(true);
    expect(screen.getByText("Writing the report")).toBeInTheDocument();
  });

  it("shows a timeout message when generation never finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ delay: null });
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(makeReport({ id: "a1", processing_status: "running" }));

    renderWithProviders(<GenerateSynopsisDialog open onOpenChange={vi.fn()} projectId="p1" />);
    await user.click(screen.getByText("Study summary"));
    await user.click(screen.getByRole("button", { name: /generate/i }));
    await screen.findByText(/generating your report/i);

    await vi.advanceTimersByTimeAsync(6 * 60 * 1000);

    expect(await screen.findByText(/taking longer than expected/i)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("offers a DOCX/PDF choice once the report is complete", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "a1", title: "Study Summary", processing_status: "completed" }),
    );
    vi.mocked(reportsService.downloadReport).mockResolvedValue(undefined);

    const user = await startGeneration();

    await user.click(await screen.findByRole("button", { name: /download docx/i }));
    expect(reportsService.downloadReport).toHaveBeenCalledWith("a1", "docx", "Study Summary.docx");
    await user.click(screen.getByRole("button", { name: /download pdf/i }));
    expect(reportsService.downloadReport).toHaveBeenCalledWith("a1", "pdf", "Study Summary.pdf");
  });

  it("shows an error when the download itself fails", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport).mockResolvedValue(
      makeReport({ id: "a1", title: "Study Summary", processing_status: "completed" }),
    );
    vi.mocked(reportsService.downloadReport).mockRejectedValue({
      status: 409,
      message: "The report is not ready for download yet.",
    });

    const user = await startGeneration();
    await user.click(await screen.findByRole("button", { name: /download docx/i }));

    expect(await screen.findByText(/not ready for download/i)).toBeInTheDocument();
  });
});
```

Run (from `frontend/`): `npx vitest run src/features/reports src/services/reports.test.ts`
Expected: FAIL. The dialog still calls `assetsService.getAsset`, so `reportsService.getReport` is never called and the pipeline text never appears.

- [ ] **Step 7: Update `frontend/src/features/reports/GenerateSynopsisDialog.tsx`**

Change the imports and types:
- remove `import * as assetsService from "@/services/assets";`
- add `import { ResearchPipeline } from "@/features/research/ResearchPipeline";`
- replace `type AssetRead = components["schemas"]["AssetRead"];` with `type ReportRead = components["schemas"]["ReportRead"];`

Change `isReportTerminal` to take `(report: ReportRead)` and read `report.processing_status`. Replace the `reportQuery` block with:

```tsx
  const reportQuery = usePolling({
    queryKey: ["reports", "report", assetId],
    queryFn: () => reportsService.getReport(assetId as string),
    isTerminal: isReportTerminal,
    enabled: assetId !== null,
    timeoutMs: POLL_TIMEOUT_MS,
  });
```

Rename `const asset = reportQuery.data;` to `const report = reportQuery.data;`, and replace every later `asset` / `asset?.` reference in the JSX with `report` / `report?.`.

Add this helper above the component (after `isReportTerminal`):

```tsx
type ReportStep = ReportRead["steps"][number];

/** The report's steps grouped by generation attempt, oldest first --
 * each attempt is its own pipeline run (1, then +1 per retry). */
function stepsByAttempt(steps: ReportStep[]): [number, ReportStep[]][] {
  const groups = new Map<number, ReportStep[]>();
  for (const step of steps) {
    groups.set(step.attempt, [...(groups.get(step.attempt) ?? []), step]);
  }
  return [...groups.entries()].sort(([a], [b]) => a - b);
}
```

Then, directly after the `{inProgress && (...)}` block, add:

```tsx
        {report && report.steps.length > 0 && (
          <div className="flex max-h-72 flex-col gap-4 overflow-y-auto rounded-lg bg-sunken p-4">
            {stepsByAttempt(report.steps).map(([attempt, steps], index, groups) => (
              <section key={attempt} aria-label={`Attempt ${attempt}`}>
                {/* Headings only once a report has been retried: a
                 * single run needs no "Attempt 1" label. */}
                {groups.length > 1 && (
                  <p className="mb-2 text-label uppercase text-muted-foreground">
                    Attempt {attempt}
                    {index === groups.length - 1 ? " (latest)" : ""}
                  </p>
                )}
                <ResearchPipeline steps={steps} />
              </section>
            ))}
          </div>
        )}
```

- [ ] **Step 8: Run the tests and the type check**

Run (from `frontend/`): `npx vitest run src/features/reports src/services/reports.test.ts src/features/research && npx tsc -b`
Expected: all pass, no type errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types/api.d.ts frontend/src/services/reports.ts frontend/src/services/reports.test.ts frontend/src/features/research/research-presentation.ts frontend/src/test/fixtures.ts frontend/src/features/reports/GenerateSynopsisDialog.tsx frontend/src/features/reports/GenerateSynopsisDialog.test.tsx
git commit -m "feat(reports): show a report run's step trace in the synopsis dialog

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Item 2 — Idempotent generation and retry

### Task 6: Atomic claim and `POST /reports/{asset_id}/retry`

**Files:**
- Modify: `backend/app/modules/reports/repository.py`
- Modify: `backend/app/workers/tasks.py` (`_generate_report`)
- Modify: `backend/app/modules/reports/service.py`
- Modify: `backend/app/modules/reports/router.py`
- Test: `backend/tests/test_reports_service.py` (extend)

**Interfaces:**
- Consumes: `ReportStepTracker` wiring (Task 3), `make_report_asset` fixture (Task 1), the test helpers `_patch_dependencies`, `_Gateway`, `_steps_for` (Task 3).
- Produces: `ReportRepository.claim_pending(asset_id: uuid.UUID) -> bool`. `_generate_report` returns `{"status": "skipped", "asset_id": ...}` when the claim fails. `ReportNotRetryableError`. `ReportService.retry_report(owner_id, asset_id) -> Asset`. Route `POST /api/v1/reports/{asset_id}/retry` → 202 `ReportGenerationAccepted` | 404 | 409 (detail `"Only a failed report can be retried."`).

- [ ] **Step 1: Write the failing tests (append to `backend/tests/test_reports_service.py`)**

```python
@pytest.mark.asyncio
async def test_a_redelivered_generation_message_does_nothing(project, monkeypatch, make_report_asset):
    from app.workers.tasks import _generate_report

    report = await make_report_asset(project)
    gateway = _Gateway()
    _patch_dependencies(monkeypatch, gateway)

    first = await _generate_report(report.id)
    calls_after_first = gateway.calls
    steps_after_first = len(await _steps_for(report.id))
    second = await _generate_report(report.id)

    assert first["status"] == "ok"
    assert second == {"status": "skipped", "asset_id": str(report.id)}
    assert gateway.calls == calls_after_first
    assert len(await _steps_for(report.id)) == steps_after_first


@pytest.mark.asyncio
async def test_a_retried_report_records_its_second_attempt_separately(session, project, monkeypatch, make_report_asset):
    from app.workers.tasks import _generate_report

    monkeypatch.setattr("app.modules.reports.service.generate_report", type("_T", (), {"delay": staticmethod(lambda asset_id: None)}))
    report = await make_report_asset(project)

    _patch_dependencies(monkeypatch, _Gateway(raises=RuntimeError("boom")))
    await _generate_report(report.id)
    await ReportService(session).retry_report(project.owner_id, report.id)
    _patch_dependencies(monkeypatch, _Gateway())
    await _generate_report(report.id)

    steps = await _steps_for(report.id)
    first = [step for step in steps if step.attempt == 1]
    second = [step for step in steps if step.attempt == 2]
    assert [step.status.value for step in first] == ["completed", "completed", "failed", "skipped"]
    assert [step.status.value for step in second] == ["completed"] * 4
    assert [step.step_index for step in second] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_claim_pending_succeeds_exactly_once(session, project, make_report_asset):
    report = await make_report_asset(project)
    repository = ReportRepository(session)

    assert await repository.claim_pending(report.id) is True
    await session.commit()
    assert await repository.claim_pending(report.id) is False


@pytest.mark.asyncio
async def test_retry_resets_a_failed_report_and_re_enqueues_it(session, project, monkeypatch, make_report_asset):
    dispatched: list[str] = []
    monkeypatch.setattr(
        "app.modules.reports.service.generate_report",
        type("_T", (), {"delay": staticmethod(lambda asset_id: dispatched.append(asset_id))}),
    )
    report = await make_report_asset(
        project,
        status=AssetProcessingStatus.FAILED,
        processing_error="Report generation failed (RuntimeError).",
    )

    retried = await ReportService(session).retry_report(project.owner_id, report.id)

    assert retried.processing_status is AssetProcessingStatus.PENDING
    assert retried.processing_error is None
    assert retried.asset_metadata["sections"] == []
    assert dispatched == [str(report.id)]


@pytest.mark.asyncio
async def test_retry_refuses_a_report_that_has_not_failed_and_hides_it_from_others(session, project, monkeypatch, make_report_asset):
    from app.modules.reports.service import ReportNotRetryableError

    monkeypatch.setattr("app.modules.reports.service.generate_report", type("_T", (), {"delay": staticmethod(lambda asset_id: None)}))
    report = await make_report_asset(project, status=AssetProcessingStatus.COMPLETED)

    with pytest.raises(ReportNotRetryableError):
        await ReportService(session).retry_report(project.owner_id, report.id)
    with pytest.raises(ReportNotFoundError):
        await ReportService(session).retry_report(uuid.uuid4(), report.id)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_service.py -k "redelivered or claim_pending or retry"`
Expected: FAIL. `claim_pending` and `retry_report` don't exist, and the second `_generate_report` run re-runs the graph.

- [ ] **Step 3: Add `claim_pending` to `backend/app/modules/reports/repository.py`**

Add `update` to the sqlalchemy import (`from sqlalchemy import select, update`), and import `AssetProcessingStatus` if it isn't already imported. Add the method to `ReportRepository`:

```python
    async def claim_pending(self, asset_id: uuid.UUID) -> bool:
        """Atomically move a report from `pending` to `running`.

        One conditional UPDATE, so exactly one caller can win: a
        duplicate or redelivered Celery message finds the row no longer
        `pending` and gets `False` -- before any LLM call or write. The
        caller commits."""
        result = await self._session.execute(
            update(Asset)
            .where(
                Asset.id == asset_id,
                Asset.processing_status == AssetProcessingStatus.PENDING,
            )
            .values(processing_status=AssetProcessingStatus.RUNNING)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
```

- [ ] **Step 4: Use the claim in `_generate_report` (`backend/app/workers/tasks.py`)**

Add `from app.modules.reports.repository import ReportRepository` to the imports. Replace the opening of `_generate_report` (from `assets = AssetRepository(session)` through the `await session.commit()` that follows `asset.processing_status = AssetProcessingStatus.RUNNING`) with:

```python
        assets = AssetRepository(session)
        # Claim first: only a `pending` report may start. A duplicate or
        # redelivered message -- or a missing asset -- claims nothing and
        # exits here, before any LLM call or write.
        claimed = await ReportRepository(session).claim_pending(asset_id)
        await session.commit()
        if not claimed:
            logger.info("report_generation_not_claimed", asset_id=str(asset_id))
            return {"status": "skipped", "asset_id": str(asset_id)}

        asset = await assets.get_by_id(asset_id)
```

(The old `asset is None` / `asset_missing` branch goes away: a missing asset can't be claimed. If a test asserts `"asset_missing"`, update it to expect `"skipped"`.)

- [ ] **Step 5: Add `retry_report` to `backend/app/modules/reports/service.py`**

Add the exception after `ReportNotReadyError`:

```python
class ReportNotRetryableError(Exception):
    """Raised when a retry is requested for a report that has not failed."""
```

Add the method after `get_report`:

```python
    async def retry_report(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        """Reset a failed report to `pending` and re-enqueue it.

        Only a `failed` report is retryable -- anything else (pending,
        running, completed) raises `ReportNotRetryableError`. Earlier
        attempts' steps are kept; the new attempt appends to the trace.
        Two concurrent retries may both enqueue, but the worker's
        atomic `pending -> running` claim lets exactly one run.
        """
        asset = await self.get_owned_report(owner_id, asset_id)
        if asset.processing_status is not AssetProcessingStatus.FAILED:
            raise ReportNotRetryableError(asset_id)

        asset.processing_status = AssetProcessingStatus.PENDING
        asset.processing_error = None
        asset.asset_metadata = {**asset.asset_metadata, "sections": []}
        await self._session.commit()

        generate_report.delay(str(asset.id))
        return asset
```

- [ ] **Step 6: Add the route to `backend/app/modules/reports/router.py`**

Add `ReportNotRetryableError` to the service import. Append:

```python
@router.post(
    "/reports/{asset_id}/retry",
    response_model=ReportGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_report_route(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportGenerationAccepted:
    """Re-run a failed report. Owner-only (404 otherwise); only a
    `failed` report is retryable (409 otherwise)."""
    try:
        asset = await service.retry_report(current_user.id, asset_id)
    except ReportNotFoundError as exc:
        raise _REPORT_NOT_FOUND from exc
    except ReportNotRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a failed report can be retried."
        ) from exc
    return ReportGenerationAccepted(asset_id=asset.id, status=asset.processing_status)
```

- [ ] **Step 7: Run the tests and restart the worker**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_service.py tests/test_reports_graph.py tests/test_report_steps.py`
Expected: all pass.
Run from the repo root: `docker compose restart worker`

- [ ] **Step 8: Commit**

```bash
git add backend/app/modules/reports/repository.py backend/app/workers/tasks.py backend/app/modules/reports/service.py backend/app/modules/reports/router.py backend/tests/test_reports_service.py
git commit -m "feat(reports): claim report generation atomically and add retry

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — Retry on a failed report

**Files:**
- Modify: `frontend/src/types/api.d.ts` (regenerated)
- Modify: `frontend/src/services/reports.ts`, `frontend/src/services/reports.test.ts`
- Modify: `frontend/src/features/reports/GenerateSynopsisDialog.tsx`, `GenerateSynopsisDialog.test.tsx`
- Modify: `frontend/src/features/assets/DocumentCard.tsx`, `DocumentCard.test.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/reports/{asset_id}/retry` (Task 6); dialog query key `["reports", "report", assetId]` (Task 5); `DocumentsSection`'s list key `["assets", projectId]`.
- Produces: `reportsService.retryReport(assetId: string): Promise<ReportGenerationAccepted>`; a button named "Retry" in the dialog (failed state) and on a failed generated `DocumentCard`.

- [ ] **Step 1: Regenerate the API types**

Run (from `frontend/`): `npm run generate:api`
Expected: `api.d.ts` gains `/api/v1/reports/{asset_id}/retry`.

- [ ] **Step 2: Write the failing tests**

Append to `frontend/src/services/reports.test.ts` (inside the `describe`):

```ts
  it("retries a failed report", async () => {
    vi.mocked(client.request).mockResolvedValue({ asset_id: "a1", status: "pending" });

    await reportsService.retryReport("a1");

    expect(client.request).toHaveBeenCalledWith("/api/v1/reports/a1/retry", { method: "POST" });
  });
```

Append to the `describe` in `GenerateSynopsisDialog.test.tsx`:

```tsx
  it("retries a failed report and resumes polling", async () => {
    vi.mocked(reportsService.generateSynopsis).mockResolvedValue({ asset_id: "a1", status: "pending" });
    vi.mocked(reportsService.getReport)
      .mockResolvedValueOnce(
        makeReport({ id: "a1", processing_status: "failed", processing_error: "Report generation failed (RuntimeError)." }),
      )
      .mockResolvedValue(makeReport({ id: "a1", processing_status: "running" }));
    vi.mocked(reportsService.retryReport).mockResolvedValue({ asset_id: "a1", status: "pending" });

    const user = await startGeneration();
    await user.click(await screen.findByRole("button", { name: /retry/i }));

    expect(reportsService.retryReport).toHaveBeenCalledWith("a1");
    expect(await screen.findByText(/generating your report/i)).toBeInTheDocument();
  });
```

Append to `DocumentCard.test.tsx` (add `vi.mock("@/services/reports");` and `import * as reportsService from "@/services/reports";` at the top, next to the existing assets mock):

```tsx
describe("DocumentCard retry", () => {
  it("offers Retry only on a failed generated report, and retries it", async () => {
    const user = userEvent.setup();
    vi.mocked(reportsService.retryReport).mockResolvedValue({ asset_id: "asset-1", status: "pending" });

    renderWithProviders(
      <DocumentCard
        asset={makeAsset({ source: "generated", processing_status: "failed" })}
        isSelected={false}
        onSelect={vi.fn()}
        projectId="project-1"
      />,
    );
    await user.click(screen.getByRole("button", { name: /^retry$/i }));

    await waitFor(() => expect(reportsService.retryReport).toHaveBeenCalledWith("asset-1"));
  });

  it("offers no Retry on a failed uploaded document", () => {
    renderWithProviders(
      <DocumentCard
        asset={makeAsset({ source: "upload", processing_status: "failed" })}
        isSelected={false}
        onSelect={vi.fn()}
        projectId="project-1"
      />,
    );

    expect(screen.queryByRole("button", { name: /^retry$/i })).not.toBeInTheDocument();
  });
});
```

(`makeAsset` here is the file's own local helper.)

Run (from `frontend/`): `npx vitest run src/services/reports.test.ts src/features/reports src/features/assets/DocumentCard.test.tsx`
Expected: the new tests FAIL (`retryReport` is not a function; no Retry button).

- [ ] **Step 3: Add `retryReport` to `frontend/src/services/reports.ts`**

```ts
export function retryReport(assetId: string) {
  return request<ReportGenerationAccepted>(`/api/v1/reports/${assetId}/retry`, { method: "POST" });
}
```

- [ ] **Step 4: Add Retry to `GenerateSynopsisDialog.tsx`**

Change the react-query import to `import { useMutation, useQueryClient } from "@tanstack/react-query";`. Inside the component, after `generateMutation`:

```tsx
  const queryClient = useQueryClient();
  // ponytail: the 6-minute poll timeout keeps counting from the first
  // attempt; a retry after a timeout needs a reopened dialog. Reset
  // `usePolling`'s timer per attempt if that ever matters.
  const retryMutation = useMutation({
    mutationFn: (id: string) => reportsService.retryReport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", "report", assetId] }),
  });
```

In `reset()`, add `retryMutation.reset();`. Replace the `{failed && (...)}` block with:

```tsx
        {failed && (
          <div className="flex flex-col gap-2">
            <p role="alert" className="text-sm text-destructive">
              {report?.processing_error ?? "Report generation failed."}
            </p>
            {retryMutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {messageFor(retryMutation.error)}
              </p>
            )}
            <div>
              <Button
                variant="outline"
                disabled={retryMutation.isPending}
                onClick={() => report && retryMutation.mutate(report.id)}
              >
                {retryMutation.isPending ? "Retrying…" : "Retry"}
              </Button>
            </div>
          </div>
        )}
```

- [ ] **Step 5: Add Retry to `frontend/src/features/assets/DocumentCard.tsx`**

Add `import * as reportsService from "@/services/reports";`. After `deleteMutation`:

```tsx
  // A failed GENERATED report can be re-run; an uploaded document's
  // failure is not retryable from here (`reprocess` rejects reports).
  const retryable = asset.source === "generated" && asset.processing_status === "failed";
  const retryMutation = useMutation({
    mutationFn: () => reportsService.retryReport(asset.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["assets", projectId] }),
  });
```

Inside the `<div className="flex flex-wrap items-center gap-1.5">` badge row, after the three `StatusBadge`s:

```tsx
              {retryable && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-xs"
                  disabled={retryMutation.isPending}
                  onClick={(event) => {
                    // The card itself is `role="button"` for selection.
                    event.stopPropagation();
                    retryMutation.mutate();
                  }}
                >
                  {retryMutation.isPending ? "Retrying…" : "Retry"}
                </Button>
              )}
```

- [ ] **Step 6: Run the tests and the type check**

Run (from `frontend/`): `npx vitest run src/services/reports.test.ts src/features/reports src/features/assets && npx tsc -b`
Expected: all pass, no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/api.d.ts frontend/src/services/reports.ts frontend/src/services/reports.test.ts frontend/src/features/reports/GenerateSynopsisDialog.tsx frontend/src/features/reports/GenerateSynopsisDialog.test.tsx frontend/src/features/assets/DocumentCard.tsx frontend/src/features/assets/DocumentCard.test.tsx
git commit -m "feat(reports): add Retry for a failed report in the dialog and document card

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Item 3 — Route-level tests

### Task 8: Drive the real app over ASGI

**Files:**
- Create: `backend/tests/test_reports_routes.py`

**Interfaces:**
- Consumes: the routes from Tasks 4 and 6 and the existing synopsis/download routes; `/api/v1/auth/register`, `/api/v1/auth/login`, `GET /api/v1/auth/me` (returns `UserRead` with `id`), `POST /api/v1/projects`.

- [ ] **Step 1: Write the tests**

```python
"""Route-level tests for the reports API (hardening item 3).

Drives the real app through `httpx.ASGITransport` against the real
database, following `tests/test_project_persona.py`. The Celery enqueue
is replaced; no LLM is ever called. Every user created here is deleted
in a `finally` (cascading to projects and assets)."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.database.session import async_session_factory
from app.main import app
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.auth.models import User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def enqueued(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.modules.reports.service.generate_report",
        SimpleNamespace(delay=lambda asset_id: calls.append(asset_id)),
    )
    return calls


async def _register(client: httpx.AsyncClient, emails: list[str]) -> tuple[dict, uuid.UUID]:
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    emails.append(email)
    await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "persona": "student"}
    )
    tokens = (
        await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return headers, uuid.UUID(me["id"])


async def _create_project(client: httpx.AsyncClient, headers: dict) -> uuid.UUID:
    created = (
        await client.post("/api/v1/projects", json={"name": "Reports route test"}, headers=headers)
    ).json()
    return uuid.UUID(created["id"])


async def _report(owner_id: uuid.UUID, project_id: uuid.UUID, status: AssetProcessingStatus) -> uuid.UUID:
    async with async_session_factory() as session:
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title="Study Summary",
            description=None,
            asset_type=AssetType.SUMMARY,
            status=AssetStatus.ACTIVE,
            mime_type="application/json",
            file_name="study-summary.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            asset_metadata={"kind": "study_summary", "sections": []},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=status,
            processing_error=(
                "Report generation failed (RuntimeError)."
                if status is AssetProcessingStatus.FAILED
                else None
            ),
        )
        session.add(asset)
        await session.commit()
        return asset.id


async def _cleanup(emails: list[str]) -> None:
    async with async_session_factory() as cleanup:
        await cleanup.execute(delete(User).where(User.email.in_(emails)))
        await cleanup.commit()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_synopsis_404s_for_another_users_project_and_422s_without_documents(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            owner_headers, _ = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, owner_headers)
            url = f"/api/v1/projects/{project_id}/reports/synopsis"

            stranger = await client.post(url, json={"kind": "study_summary"}, headers=stranger_headers)
            empty = await client.post(url, json={"kind": "study_summary"}, headers=owner_headers)

            assert stranger.status_code == 404
            assert empty.status_code == 422
            assert enqueued == []
            async with async_session_factory() as session:
                count = await session.scalar(
                    select(func.count()).select_from(Asset).where(Asset.project_id == project_id)
                )
            assert count == 0
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_download_404s_for_a_non_owner_and_409s_until_complete():
    emails: list[str] = []
    async with _client() as client:
        try:
            owner_headers, owner_id = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, owner_headers)
            report_id = await _report(owner_id, project_id, AssetProcessingStatus.RUNNING)
            url = f"/api/v1/reports/{report_id}/download?format=docx"

            stranger = await client.get(url, headers=stranger_headers)
            not_ready = await client.get(url, headers=owner_headers)

            assert stranger.status_code == 404
            assert not_ready.status_code == 409
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_retry_404s_for_a_non_owner_409s_unless_failed_and_202s_a_failed_report(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            owner_headers, owner_id = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, owner_headers)
            completed_id = await _report(owner_id, project_id, AssetProcessingStatus.COMPLETED)
            failed_id = await _report(owner_id, project_id, AssetProcessingStatus.FAILED)

            stranger = await client.post(f"/api/v1/reports/{failed_id}/retry", headers=stranger_headers)
            not_failed = await client.post(f"/api/v1/reports/{completed_id}/retry", headers=owner_headers)
            accepted = await client.post(f"/api/v1/reports/{failed_id}/retry", headers=owner_headers)

            assert stranger.status_code == 404
            assert not_failed.status_code == 409
            assert accepted.status_code == 202
            assert accepted.json() == {"asset_id": str(failed_id), "status": "pending"}
            assert enqueued == [str(failed_id)]

            report = await client.get(f"/api/v1/reports/{failed_id}", headers=owner_headers)
            assert report.status_code == 200
            assert report.json()["processing_status"] == "pending"
            assert report.json()["processing_error"] is None
            assert report.json()["steps"] == []
            assert (
                await client.get(f"/api/v1/reports/{failed_id}", headers=stranger_headers)
            ).status_code == 404
        finally:
            await _cleanup(emails)
```

- [ ] **Step 2: Run the tests**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_reports_routes.py`
Expected: 3 passed. (These pin down routes that already exist, so they're expected to pass immediately. To check they really exercise the code, temporarily change the 409 in `retry_report_route` to 400, confirm `test_retry_…` fails, then revert. Record that RED check in the report.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_reports_routes.py
git commit -m "test(reports): route-level tests for synopsis, download, and retry

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Item 4 — Direct tests for the database-backed helpers

### Task 9: `RepositoryDocumentLister` and `KnowledgeBaseSectionSearcher` against the real DB

**Files:**
- Modify: `backend/app/agents/reports/nodes.py` (the two ABCs and two implementations)
- Create: `backend/tests/test_report_dependencies.py`

**Interfaces:**
- Produces: `DocumentLister.list_processed(project_id: str | uuid.UUID)`; `SectionSearcher.search(*, owner_id: str | uuid.UUID, project_id: str | uuid.UUID, query: str, limit: int)`; `KnowledgeBaseSectionSearcher(session, *, knowledge_base: KnowledgeBaseService | None = None)`.

- [ ] **Step 1: Write the failing tests `backend/tests/test_report_dependencies.py`**

```python
"""Direct, database-backed tests for the report graph's two real
helpers (hardening item 4): `RepositoryDocumentLister` and
`KnowledgeBaseSectionSearcher`. Real Postgres + pgvector; a fixed-vector
fake embedding provider stands in for the model, and reranking is
switched off, so no model is ever called."""

import uuid

import pytest

from app.agents.reports.nodes import KnowledgeBaseSectionSearcher, RepositoryDocumentLister
from app.core.config import settings
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import (
    AssetProcessingStatus,
    AssetSource,
    AssetStatus,
    AssetType,
    EmbeddingStatus,
)
from app.modules.assets.models import Asset
from app.modules.knowledge_base.embeddings import EmbeddingProvider
from app.modules.knowledge_base.enums import EmbeddingProviderName
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.service import KnowledgeBaseService
from app.modules.projects.models import Project, ProjectStatus, ProjectType

DIM = 1024


def _vector() -> list[float]:
    vector = [0.001] * DIM
    vector[0] = 1.0
    return vector


class _FixedEmbeddings(EmbeddingProvider):
    @property
    def name(self) -> EmbeddingProviderName:
        return EmbeddingProviderName.LOCAL

    @property
    def dimensions(self) -> int:
        return DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector() for _ in texts]


def _document(project: Project, *, title: str, status=AssetProcessingStatus.COMPLETED, asset_type=AssetType.DOCUMENT, source=AssetSource.UPLOAD) -> Asset:
    return Asset(
        project_id=project.id,
        owner_id=project.owner_id,
        title=title,
        description=None,
        asset_type=asset_type,
        status=AssetStatus.ACTIVE,
        mime_type="application/pdf",
        file_name=f"{title.lower().replace(' ', '-')}.pdf",
        file_extension="pdf",
        file_size=10,
        storage_path="projects/x/doc.pdf",
        checksum=uuid.uuid4().hex,
        source=source,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile(summary=f"About {title}.", topics=["poultry"]).model_dump(mode="json"),
        created_by=project.owner_id,
        processing_status=status,
    )


async def _other_project(session, project: Project) -> Project:
    other = Project(
        owner_id=project.owner_id,
        name="Another project",
        description=None,
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(other)
    await session.commit()
    return other


@pytest.mark.asyncio
async def test_document_lister_returns_only_this_projects_processed_documents(session, project):
    other = await _other_project(session, project)
    processed = _document(project, title="Processed Paper")
    session.add_all(
        [
            processed,
            _document(project, title="Still Queued", status=AssetProcessingStatus.QUEUED),
            _document(project, title="A Summary", asset_type=AssetType.SUMMARY, source=AssetSource.GENERATED),
            _document(other, title="Other Project Paper"),
        ]
    )
    await session.commit()

    lister = RepositoryDocumentLister(session)
    for project_id in (str(project.id), project.id):
        documents = await lister.list_processed(project_id)

        assert [document["asset_id"] for document in documents] == [str(processed.id)]
        assert documents[0]["title"] == "Processed Paper"
        assert documents[0]["summary"] == "About Processed Paper."


@pytest.mark.asyncio
async def test_section_searcher_returns_only_this_projects_evidence_for_str_and_uuid_ids(session, project, monkeypatch):
    monkeypatch.setattr(settings, "reranker_enabled", False)
    other = await _other_project(session, project)
    mine = _document(project, title="My Paper")
    theirs = _document(other, title="Their Paper")
    session.add_all([mine, theirs])
    await session.flush()
    session.add_all(
        [
            KnowledgeChunk(
                project_id=project.id,
                asset_id=mine.id,
                chunk_index=0,
                content="my poultry evidence",
                embedding_status=EmbeddingStatus.COMPLETED,
                embedding_provider=EmbeddingProviderName.LOCAL,
                embedding=_vector(),
            ),
            KnowledgeChunk(
                project_id=other.id,
                asset_id=theirs.id,
                chunk_index=0,
                content="other project's poultry evidence",
                embedding_status=EmbeddingStatus.COMPLETED,
                embedding_provider=EmbeddingProviderName.LOCAL,
                embedding=_vector(),
            ),
        ]
    )
    await session.commit()

    searcher = KnowledgeBaseSectionSearcher(
        session, knowledge_base=KnowledgeBaseService(session, embeddings=_FixedEmbeddings())
    )
    for owner_id, project_id in ((str(project.owner_id), str(project.id)), (project.owner_id, project.id)):
        evidence = await searcher.search(owner_id=owner_id, project_id=project_id, query="poultry", limit=5)

        assert [item["asset_id"] for item in evidence] == [str(mine.id)]
        assert evidence[0]["snippet"] == "my poultry evidence"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_report_dependencies.py`
Expected: FAIL. The UUID-typed call raises `AttributeError: 'UUID' object has no attribute 'replace'` (from `uuid.UUID(uuid_obj)`), and `KnowledgeBaseSectionSearcher` rejects the `knowledge_base` keyword.

- [ ] **Step 3: Update `backend/app/agents/reports/nodes.py`**

Add after the `_JSON_FENCE` definition:

```python
def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Accept a raw state string or an already-parsed UUID."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
```

Replace the two ABCs' method signatures and docstrings:

```python
class DocumentLister(ABC):
    """Contract for listing a project's processed documents.

    Accepts the raw `ReportState["project_id"]` string or a `uuid.UUID`,
    so a fake in tests can use any id shape and a database-backed
    implementation parses it itself."""

    @abstractmethod
    async def list_processed(self, project_id: str | uuid.UUID) -> list[ProcessedDocument]:
        """Return every processed document available to draw a report from."""


class SectionSearcher(ABC):
    """Contract for retrieving evidence for one report section.

    `owner_id`/`project_id` accept a raw state string or a `uuid.UUID`,
    for the same reason as `DocumentLister.list_processed`."""

    @abstractmethod
    async def search(
        self, *, owner_id: str | uuid.UUID, project_id: str | uuid.UUID, query: str, limit: int
    ) -> list[SectionEvidence]:
        """Return the best-matching excerpts for `query`, owner-scoped."""
```

In `RepositoryDocumentLister.list_processed`, change the signature to `project_id: str | uuid.UUID`, and the query to `await self._repository.list_processed_documents(_as_uuid(project_id))`.

Replace `KnowledgeBaseSectionSearcher.__init__` and the signature/first lines of `search` with:

```python
    def __init__(
        self, session: AsyncSession, *, knowledge_base: KnowledgeBaseService | None = None
    ) -> None:
        # `knowledge_base` is injectable so a database-backed test can
        # supply a fixed-vector embedding provider -- no model call.
        self._service = knowledge_base or KnowledgeBaseService(session)

    async def search(
        self, *, owner_id: str | uuid.UUID, project_id: str | uuid.UUID, query: str, limit: int
    ) -> list[SectionEvidence]:
        outcome = await self._service.two_stage_search(
            _as_uuid(owner_id), query=query, project_id=_as_uuid(project_id), top_k=limit
        )
```

(Leave the rest of `search` unchanged.)

- [ ] **Step 4: Run the tests**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_report_dependencies.py tests/test_reports_graph.py tests/test_reports_service.py`
Expected: all pass. If fixture teardown fails on a foreign key from `knowledge_chunks`, delete the two chunks and the other project explicitly at the end of the searcher test (the same way `test_reranking._cleanup_chunks` does) and note that in the report.

- [ ] **Step 5: Restart the worker and commit**

Run from the repo root: `docker compose restart worker`

```bash
git add backend/app/agents/reports/nodes.py backend/tests/test_report_dependencies.py
git commit -m "test(reports): cover the document lister and section searcher against the real DB

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Item 1 (addendum) — Reconciler closes a stale report's open steps

### Task 10: A stale report's `running` steps are marked failed

**Files:**
- Modify: `backend/app/workers/reconciliation.py` (`reconcile_stale_report_generations`)
- Test: `backend/tests/test_report_generation_reconciliation.py` (extend)

**Interfaces:**
- Consumes: `ResearchStep.asset_id`/`attempt` (Task 1), `ResearchStepStatus`.
- Produces: `STALE_REPORT_STEP_ERROR = "Worker stopped before this step finished"` in `app.workers.reconciliation`. When the reconciler fails a stale report asset, it also sets every one of that asset's `running` steps to `failed`, with `error_message=STALE_REPORT_STEP_ERROR` and `completed_at` set. Steps already `completed`/`failed`/`skipped`, and every step of a non-stale report, are left alone.

- [ ] **Step 1: Write the failing test (append to `backend/tests/test_report_generation_reconciliation.py`)**

Add to the imports: `from app.modules.research.enums import ResearchStepStatus`, `from app.modules.research.models import ResearchStep`, and add `STALE_REPORT_STEP_ERROR` to the existing `from app.workers.reconciliation import (...)` list. Append:

```python
def _report_step(asset: Asset, index: int, status: ResearchStepStatus) -> ResearchStep:
    return ResearchStep(
        asset_id=asset.id,
        attempt=1,
        step_index=index,
        node_name=["collect_documents", "retrieve_evidence"][index],
        title=["Collect project documents", "Retrieve section evidence"][index],
        status=status,
        started_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_a_stale_reports_running_step_is_marked_failed_and_nothing_else_is_touched(session, project):
    stale = await _make_report_asset(session, project, updated_at=_stale_timestamp())
    fresh = await _make_report_asset(session, project, updated_at=_fresh_timestamp())
    finished = _report_step(stale, 0, ResearchStepStatus.COMPLETED)
    interrupted = _report_step(stale, 1, ResearchStepStatus.RUNNING)
    in_progress = _report_step(fresh, 0, ResearchStepStatus.RUNNING)
    session.add_all([finished, interrupted, in_progress])
    await session.commit()

    await reconcile_stale_report_generations()

    for step in (finished, interrupted, in_progress):
        await session.refresh(step)
    assert interrupted.status is ResearchStepStatus.FAILED
    assert interrupted.error_message == STALE_REPORT_STEP_ERROR == "Worker stopped before this step finished"
    assert interrupted.completed_at is not None
    assert finished.status is ResearchStepStatus.COMPLETED
    assert finished.error_message is None
    assert in_progress.status is ResearchStepStatus.RUNNING
```

(Adding steps doesn't change the asset's `updated_at`, which lives on a different table, so `stale` stays stale.)

- [ ] **Step 2: Run it and watch it fail**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_report_generation_reconciliation.py`
Expected: FAIL with `ImportError: cannot import name 'STALE_REPORT_STEP_ERROR'`.

- [ ] **Step 3: Update `backend/app/workers/reconciliation.py`**

Add `update` to the file's existing `from sqlalchemy import ...` line. Add the imports `from app.modules.research.enums import ResearchStepStatus` and `from app.modules.research.models import ResearchStep` (unless already imported). Directly after `STALE_REPORT_FAILURE_REASON`, add:

```python
#: Recorded on every step a stale report left `running` -- the worker
#: died mid-step, so the step never reached its own completion or
#: failure write. Fixed text, no interpolation, same discipline as
#: `STALE_REPORT_FAILURE_REASON`.
STALE_REPORT_STEP_ERROR = "Worker stopped before this step finished"
```

In `reconcile_stale_report_generations`, inside the `for asset in stale_assets:` loop, directly after `asset.processing_error = STALE_REPORT_FAILURE_REASON`, add:

```python
            # Close the trace too: the step the worker died in would
            # otherwise show `running` forever next to a failed report.
            await session.execute(
                update(ResearchStep)
                .where(
                    ResearchStep.asset_id == asset.id,
                    ResearchStep.status == ResearchStepStatus.RUNNING,
                )
                .values(
                    status=ResearchStepStatus.FAILED,
                    error_message=STALE_REPORT_STEP_ERROR,
                    completed_at=datetime.now(timezone.utc),
                )
                .execution_options(synchronize_session=False)
            )
```

Append this sentence to the function's docstring: "A reconciled report's `running` steps are failed with `STALE_REPORT_STEP_ERROR` in the same transaction, so the trace never shows a step still running on a failed report."

- [ ] **Step 4: Run the tests and restart the worker**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q tests/test_report_generation_reconciliation.py tests/test_research_run_reconciliation.py`
Expected: all pass.
Run from the repo root: `docker compose restart worker`

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/reconciliation.py backend/tests/test_report_generation_reconciliation.py
git commit -m "fix(reports): fail a stale report's running steps during reconciliation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Item 5 — Full verification

### Task 11: Full backend and frontend suites

**Files:** none unless a failure needs a fix.

- [ ] **Step 1: Full backend suite**

Run: `MSYS_NO_PATHCONV=1 docker exec aikdap_backend python -m pytest -q -rfE`
Expected: every failure or error is in the known environmental set: `tests/test_execution_*` (no Docker in the container), `tests/test_cross_paper.py`, `tests/test_fallback_grounding.py`. Record the counts line and the full list of FAILED/ERROR node ids in the report.

- [ ] **Step 2: Investigate anything outside the known set**

For each failure outside that set, use superpowers:systematic-debugging. Reproduce it alone (`python -m pytest -q <nodeid>`), find the root cause, fix it at the root, and re-run the full suite. In particular, confirm that these tests from step 4's final review pass: the `tests/test_assets*` storage/reprocess guards, `reconcile_stale_report_generations` in the reconciliation tests, and `tests/test_reports_*`. Any fix commits as `fix(...)` with the Co-Authored-By line, and must not change unrelated behaviour.

- [ ] **Step 3: Full frontend suite and build**

Run (from `frontend/`): `npm test && npm run build`
Expected: all tests pass and the build succeeds. If only `Landing.test.tsx` "tells the whole pipeline story" fails, re-run that file alone (`npx vitest run src/pages/Landing.test.tsx`) three times. If it passes each time, record it as the known load-flaky test; any other failure is investigated as in Step 2. Confirm `src/features/assets/asset-state.test.ts` (the `isSettled` fix) passes.

- [ ] **Step 4: Record the results**

No commit unless Step 2 or 3 produced a fix. Report the exact counts lines from both suites.

---

## Self-Review Notes

- **Coverage of the six items:**
  - Item 1:
    - trace per node (collect, retrieve, write, coverage) with title/status/summary/duration/scrubbed error: Tasks 2–3;
    - reuse of `research_steps` via a nullable reference: Task 1;
    - steps on the report's API response: Task 4;
    - shown in the same pipeline view research runs use: Task 5;
    - failed node → asset failed, step failed, no partial document: Task 3 tests.
  - Item 2: atomic `pending→running` claim and redelivery no-op: Task 6; retry endpoint 404/409/202: Tasks 6 and 8; frontend Retry: Task 7.
  - Item 3: Task 8.
  - Item 4: Task 9.
  - Item 5: Task 11.
  - Item 6: skipped by the user.
  - User change 1, the attempt column and grouping: Task 1 (column, ordering, `latest_attempt`), Task 3 (tracker/task numbering), Task 6 (retry records attempt 2), Task 5 (dialog groups by attempt).
  - User change 2, the reconciler failing running steps: Task 10.
  - Confirmations: asset_id CASCADE is tested in Task 1; `list_by_run` is the only run-scoped step query.
- **Type consistency:**
  - `list_by_asset(asset_id)` (Task 1) is used by Tasks 3 and 4.
  - `ReportStepTracker(session, asset_id, *, start_index)` and `scrub_report_error` (Task 3) are used by `tasks.py`.
  - `ReportRead.from_report(asset, steps)` (Task 4) is used by the router.
  - Query key `["reports", "report", assetId]` is set in Task 5 and invalidated in Task 7.
  - `makeReport`/`makeStep` (Task 5) are used in Tasks 5 and 7.
  - Test helpers `_patch_dependencies`/`_Gateway`/`_steps_for` are defined in Task 3 and reused in Task 6.
- **Placeholder scan:** none. Every code step has full code; every run step has an exact command and expected result.
