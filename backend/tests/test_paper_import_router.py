"""Milestone 10 step 3 (Add & re-run): `ResearchService.import_papers`
-- the business logic `POST /research/runs/{run_id}/papers/import`
delegates to. Ownership itself is `get_owned_run`'s existing, already-
tested 404 contract (untouched by this feature); this file covers the
id-validation and dispatch behaviour that is new.
"""

import pytest

from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
from app.modules.research.service import (
    PaperNotOpenAccessError,
    ResearchService,
    UnknownSuggestedPaperError,
)

pytestmark = pytest.mark.asyncio


async def _make_run(session, project, *, suggested_papers) -> ResearchRun:
    run = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query="A research question?",
        status=ResearchRunStatus.COMPLETED,
        include_assets=True,
        include_web=True,
        max_results=5,
        suggested_papers=suggested_papers,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


def _paper(openalex_id: str, *, oa_pdf_url: str | None) -> dict:
    return {
        "openalex_id": openalex_id,
        "title": "A Suggested Paper",
        "authors": [],
        "year": 2024,
        "cited_by_count": 0,
        "landing_url": "https://example.org/landing",
        "oa_pdf_url": oa_pdf_url,
        "relevance_note": "note",
    }


@pytest.fixture(autouse=True)
def _no_real_dispatch(monkeypatch):
    """`import_papers` calls `dispatch_paper_import` at the end --
    stubbed here so these tests exercise validation and the
    `import_status="queued"` write only, not the live broker."""
    from app.workers import tasks as tasks_module

    dispatched: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(
        tasks_module,
        "dispatch_paper_import",
        lambda run_id, papers: dispatched.append((run_id, papers)),
    )
    return dispatched


async def test_rejects_an_id_that_is_not_a_suggested_paper(session, project):
    run = await _make_run(session, project, suggested_papers=[_paper("W1", oa_pdf_url="https://x/a.pdf")])
    service = ResearchService(session)

    with pytest.raises(UnknownSuggestedPaperError):
        await service.import_papers(run, ["W999"])


async def test_rejects_a_paper_with_no_open_access_pdf(session, project):
    run = await _make_run(session, project, suggested_papers=[_paper("W1", oa_pdf_url=None)])
    service = ResearchService(session)

    with pytest.raises(PaperNotOpenAccessError):
        await service.import_papers(run, ["W1"])


async def test_accepts_valid_ids_and_marks_them_queued(session, project, _no_real_dispatch):
    run = await _make_run(
        session,
        project,
        suggested_papers=[
            _paper("W1", oa_pdf_url="https://x/a.pdf"),
            _paper("W2", oa_pdf_url="https://x/b.pdf"),
        ],
    )
    service = ResearchService(session)

    result = await service.import_papers(run, ["W1", "W2"])

    assert {p["openalex_id"] for p in result} == {"W1", "W2"}
    assert all(p["status"] == "queued" for p in result)

    await session.refresh(run)
    assert {p["openalex_id"]: p["import_status"] for p in run.suggested_papers} == {
        "W1": "queued",
        "W2": "queued",
    }


async def test_dispatches_the_import_chord_exactly_once(session, project, _no_real_dispatch):
    run = await _make_run(
        session, project, suggested_papers=[_paper("W1", oa_pdf_url="https://x/a.pdf")]
    )
    service = ResearchService(session)

    await service.import_papers(run, ["W1"])

    assert len(_no_real_dispatch) == 1
    dispatched_run_id, dispatched_papers = _no_real_dispatch[0]
    assert dispatched_run_id == str(run.id)
    assert [p["openalex_id"] for p in dispatched_papers] == ["W1"]


async def test_duplicate_ids_in_the_request_are_deduplicated(session, project, _no_real_dispatch):
    run = await _make_run(
        session, project, suggested_papers=[_paper("W1", oa_pdf_url="https://x/a.pdf")]
    )
    service = ResearchService(session)

    result = await service.import_papers(run, ["W1", "W1"])

    assert len(result) == 1
    _, dispatched_papers = _no_real_dispatch[0]
    assert len(dispatched_papers) == 1
