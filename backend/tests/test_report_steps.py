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
