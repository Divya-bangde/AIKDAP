"""Project paper map: graph building and outside-work selection (Phase 4)."""

import uuid

import pytest

from app.modules.assets.enums import AssetSource
from app.modules.assets.models import Asset
from app.modules.papers.models import PaperReference
from app.modules.papers.openalex import OpenAlexLookupError
from app.modules.papers.service import (
    MAX_EXTERNAL_NODES,
    PaperGraphService,
    build_paper_graph,
    frequent_external_ids,
)


def _paper(openalex_id, refs=(), *, status="matched", source=AssetSource.UPLOAD, title="A paper"):
    asset = Asset(id=uuid.uuid4(), title=f"{title}.pdf", source=source)
    if status is None:
        return asset, None
    reference = PaperReference(
        asset_id=asset.id,
        openalex_id=openalex_id if status == "matched" else None,
        title=title,
        publication_year=2021,
        cited_by_count=10,
        referenced_works=list(refs),
        openalex_status=status,
    )
    return asset, reference


def test_links_only_between_matched_project_papers():
    papers = [
        _paper("W1", ["W2", "W99"]),
        _paper("W2", ["W1"], source=AssetSource.IMPORTED),
        _paper(None, status="not_found", title="Lost"),
        _paper(None, status=None, title="Fresh"),
    ]

    graph = build_paper_graph(papers)

    assert {(n.id, n.kind, n.in_project) for n in graph.nodes} == {
        ("W1", "uploaded", True),
        ("W2", "suggested", True),
    }
    assert {(link.source, link.target) for link in graph.links} == {("W1", "W2"), ("W2", "W1")}
    assert [(u.title, u.status) for u in graph.unmatched] == [
        ("Lost.pdf", "not_found"),
        ("Fresh.pdf", "pending"),
    ]


def test_duplicate_uploads_of_one_work_make_one_node():
    graph = build_paper_graph([_paper("W1"), _paper("W1")])
    assert [n.id for n in graph.nodes] == ["W1"]


def test_frequent_external_ids_needs_two_citers_and_caps_at_fifteen():
    many = [f"W{100 + i}" for i in range(20)]
    papers = [
        _paper("W1", ["W2", "W50", *many]),
        _paper("W2", [*many, "W50", "W1"]),
        _paper("W3", ["W50"]),
    ]

    ids = frequent_external_ids(papers)

    assert "W2" not in ids  # a project paper is never "external"
    assert ids[0] == "W50"  # three citers outrank two
    assert len(ids) == MAX_EXTERNAL_NODES


def test_external_nodes_and_their_links():
    papers = [_paper("W1", ["W9"]), _paper("W2", ["W9"])]
    work = {
        "openalex_id": "W9",
        "title": "Classic",
        "publication_year": 1999,
        "cited_by_count": 5000,
        "doi": None,
        "referenced_works": [],
    }

    graph = build_paper_graph(papers, [work])

    external = next(n for n in graph.nodes if n.id == "W9")
    assert (external.kind, external.in_project, external.title) == ("external", False, "Classic")
    assert {(link.source, link.target) for link in graph.links} == {("W1", "W9"), ("W2", "W9")}


class _Client:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def get_many(self, ids):
        self.calls.append(ids)
        if self.fail:
            raise OpenAlexLookupError("down")
        return [{"id": f"https://openalex.org/{i}", "display_name": f"Outside {i}"} for i in ids]


class _Repo:
    def __init__(self, papers):
        self.papers = papers

    async def list_project_pdfs(self, project_id):
        return self.papers


def _service(client, papers):
    service = PaperGraphService.__new__(PaperGraphService)
    service._client = client
    service._references = _Repo(papers)

    async def owned(owner_id, project_id):
        return None

    service._ensure_project_owned = owned
    return service


@pytest.mark.asyncio
async def test_get_graph_batches_one_external_lookup():
    client = _Client()
    papers = [_paper("W1", ["W9"]), _paper("W2", ["W9"])]

    graph = await _service(client, papers).get_graph(uuid.uuid4(), uuid.uuid4(), include_external=True)

    assert client.calls == [["W9"]]
    assert any(n.id == "W9" and n.title == "Outside W9" for n in graph.nodes)


@pytest.mark.asyncio
async def test_get_graph_degrades_when_openalex_is_down():
    papers = [_paper("W1", ["W9"]), _paper("W2", ["W9"])]

    graph = await _service(_Client(fail=True), papers).get_graph(
        uuid.uuid4(), uuid.uuid4(), include_external=True
    )

    assert graph.external_unavailable is True
    assert {n.id for n in graph.nodes} == {"W1", "W2"}


@pytest.mark.asyncio
async def test_get_graph_skips_openalex_without_the_toggle():
    client = _Client()
    await _service(client, [_paper("W1", ["W9"]), _paper("W2", ["W9"])]).get_graph(
        uuid.uuid4(), uuid.uuid4(), include_external=False
    )
    assert client.calls == []
