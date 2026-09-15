"""Data-access layer for the research module.

One repository per aggregate root, matching the rest of the codebase.
Contains only persistence operations; transaction boundaries (commit),
ownership rules, and the run lifecycle live in `service.py`.

No repository here exposes a `delete()`: a research run is an audit
record of work the platform performed, and the constitution requires
that trace to remain queryable. Rows are removed only by cascade when
their project or owner is deleted.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import AgentMessage, ResearchRun, ResearchStep


class ResearchRunRepository:
    """Encapsulates all direct database access for `ResearchRun` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, run_id: uuid.UUID) -> ResearchRun | None:
        """Fetch a run by primary key, or None if not found."""
        return await self._session.get(ResearchRun, run_id)

    async def get_by_id_for_update(self, run_id: uuid.UUID) -> ResearchRun | None:
        """Fetch a run by primary key with a row lock (`SELECT ... FOR
        UPDATE`), for callers that read-modify-write a JSONB column
        (e.g. `suggested_papers`) from concurrent Celery tasks -- see
        `tasks._update_paper_import_status`. Serializes the
        read-rebuild-commit cycle so two papers finishing at the same
        moment cannot silently clobber each other's status.
        """
        return await self._session.get(ResearchRun, run_id, with_for_update=True)

    async def list_by_owner(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        status: ResearchRunStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ResearchRun]:
        """List runs owned by a user, newest first, with optional filters."""
        stmt = select(ResearchRun).where(ResearchRun.owner_id == owner_id)

        if project_id is not None:
            stmt = stmt.where(ResearchRun.project_id == project_id)
        if status is not None:
            stmt = stmt.where(ResearchRun.status == status)

        stmt = stmt.order_by(ResearchRun.created_at.desc()).offset(skip).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, run: ResearchRun) -> ResearchRun:
        """Insert a new run row and flush to populate generated fields."""
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def find_latest_child(
        self, parent_run_id: uuid.UUID, *, matching_query: str | None = None
    ) -> ResearchRun | None:
        """Find the most recently created run linked to `parent_run_id`
        via `parent_run_id`.

        `parent_run_id` is shared by two features: follow-up questions
        (a new, user-typed `query`) and this feature's paper-import
        re-run (`query` copied verbatim from the parent). When
        `matching_query` is given, only a child whose `query` matches
        it exactly is considered -- a heuristic, not a guarantee: a
        follow-up whose typed question happens to exactly match its
        parent's would be indistinguishable from a real re-run. This
        trade-off was chosen over a new migration/column
        (Milestone 10 step 3 was scoped to add none); a dedicated
        column distinguishing "why was this child created" would
        remove the ambiguity entirely and is the correct follow-up if
        this edge case ever proves to matter in practice.
        """
        stmt = (
            select(ResearchRun)
            .where(ResearchRun.parent_run_id == parent_run_id)
        )
        if matching_query is not None:
            stmt = stmt.where(ResearchRun.query == matching_query)
        stmt = stmt.order_by(ResearchRun.created_at.desc()).limit(1)
        result = await self._session.execute(stmt)
        return result.scalars().first()


class ResearchStepRepository:
    """Encapsulates all direct database access for `ResearchStep` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_run(self, run_id: uuid.UUID) -> list[ResearchStep]:
        """List a run's steps in execution order."""
        stmt = (
            select(ResearchStep)
            .where(ResearchStep.run_id == run_id)
            .order_by(ResearchStep.step_index)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

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

    async def create(self, step: ResearchStep) -> ResearchStep:
        """Insert a new step row and flush to populate generated fields."""
        self._session.add(step)
        await self._session.flush()
        await self._session.refresh(step)
        return step


class AgentMessageRepository:
    """Encapsulates all direct database access for `AgentMessage` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_run(self, run_id: uuid.UUID) -> list[AgentMessage]:
        """List a run's transcript in emission order."""
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.run_id == run_id)
            .order_by(AgentMessage.sequence)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def bulk_create(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """Insert multiple transcript rows and flush to populate generated fields."""
        if not messages:
            return []
        self._session.add_all(messages)
        await self._session.flush()
        for message in messages:
            await self._session.refresh(message)
        return messages
