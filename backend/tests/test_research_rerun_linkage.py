"""Milestone 10 step 3 (Add & re-run): a run's detail response exposes
`rerun_run_id` (the run that resulted from importing papers into it,
if any) and `added_paper_count` (when this run itself is such a
re-run, how many papers were added before it started) -- both computed
at read time from existing columns, no new schema.
"""

import pytest

from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
from app.modules.research.repository import ResearchRunRepository
from app.modules.research.service import ResearchService

pytestmark = pytest.mark.asyncio


async def _make_run(session, project, **overrides) -> ResearchRun:
    run = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query=overrides.pop("query", "A research question?"),
        status=overrides.pop("status", ResearchRunStatus.COMPLETED),
        include_assets=True,
        include_web=True,
        max_results=5,
        **overrides,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


async def test_find_rerun_id_is_none_without_a_child_run(session, project):
    run = await _make_run(session, project)
    service = ResearchService(session)

    assert await service.find_rerun_id(run) is None


async def test_find_rerun_id_returns_the_child_run(session, project):
    original = await _make_run(session, project)
    child = await _make_run(session, project, parent_run_id=original.id, query=original.query)

    service = ResearchService(session)
    assert await service.find_rerun_id(original) == child.id


async def test_find_rerun_id_returns_the_most_recent_child(session, project):
    original = await _make_run(session, project)
    await _make_run(session, project, parent_run_id=original.id, query=original.query)
    newest = await _make_run(session, project, parent_run_id=original.id, query=original.query)

    service = ResearchService(session)
    assert await service.find_rerun_id(original) == newest.id


async def test_find_rerun_id_ignores_a_followup_with_a_different_query(session, project):
    """Final review Fix 4: `parent_run_id` is shared by paper-import
    re-runs (query copied verbatim from the parent) and pre-existing
    follow-up questions (a new, user-typed query). A follow-up child
    must not be mistaken for a re-run link -- `find_rerun_id` filters
    on query equality, so a child with a different query is ignored."""
    original = await _make_run(session, project, query="Original question?")
    await _make_run(
        session, project, parent_run_id=original.id, query="A totally different follow-up question?"
    )

    service = ResearchService(session)
    assert await service.find_rerun_id(original) is None


async def test_added_paper_count_counts_only_added_status(session, project):
    original = await _make_run(
        session,
        project,
        suggested_papers=[
            {"openalex_id": "W1", "import_status": "added"},
            {"openalex_id": "W2", "import_status": "failed"},
            {"openalex_id": "W3", "import_status": "added"},
        ],
    )

    service = ResearchService(session)
    assert await service.get_added_paper_count(original.id) == 2


async def test_added_paper_count_is_none_without_suggested_papers(session, project):
    run = await _make_run(session, project)
    service = ResearchService(session)

    assert await service.get_added_paper_count(run.id) is None
