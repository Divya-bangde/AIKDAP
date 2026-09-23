"""Paper identity (Phase 1c): DOI extraction, the mocked OpenAlex client,
and the never-guess title match. No real HTTP."""

import httpx
import pytest

from app.modules.papers.openalex import (
    OpenAlexClient,
    OpenAlexLookupError,
    extract_doi,
    parse_work,
)
from app.modules.papers.service import best_title_match, title_similarity

WORK = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.7717/PEERJ.4375",
    "display_name": "The state of OA: a large-scale analysis",
    "publication_year": 2018,
    "cited_by_count": 1200,
    "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Available at https://doi.org/10.7717/PeerJ.4375.", "10.7717/peerj.4375"),
        ("DOI: 10.1016/j.cell.2020.01.001, received", "10.1016/j.cell.2020.01.001"),
        ("(see 10.1000/xyz123)", "10.1000/xyz123"),
        ("doi 10.1002/(SICI)1097-4571(199806)49:8<693", "10.1002/(sici)1097-4571(199806)49:8"),
        ("no identifier here, just 10.12/short", None),
    ],
)
def test_extract_doi(text, expected):
    assert extract_doi(text) == expected


def test_parse_work_stores_short_ids_and_bare_doi():
    assert parse_work(WORK) == {
        "openalex_id": "W2741809807",
        "doi": "10.7717/peerj.4375",
        "title": "The state of OA: a large-scale analysis",
        "publication_year": 2018,
        "cited_by_count": 1200,
        "referenced_works": ["W1", "W2"],
    }


def _client(handler, cache=None) -> OpenAlexClient:
    return OpenAlexClient(
        api_key=None, transport=httpx.MockTransport(handler), cache={} if cache is None else cache,
        backoff_base=0,
    )


@pytest.mark.asyncio
async def test_get_by_doi_uses_the_doi_path_and_caches():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=WORK)

    cache: dict = {}
    client = _client(handler, cache)
    assert (await client.get_by_doi("10.7717/peerj.4375"))["id"] == WORK["id"]
    assert (await client.get_by_doi("10.7717/peerj.4375"))["id"] == WORK["id"]
    assert calls == ["/works/doi:10.7717/peerj.4375"]  # second call served from cache


@pytest.mark.asyncio
async def test_404_is_none_and_429_is_retried():
    responses = iter([httpx.Response(429), httpx.Response(404)])
    client = _client(lambda request: next(responses))
    assert await client.get_by_id("W999") is None


@pytest.mark.asyncio
async def test_persistent_429_raises_without_leaking_the_url():
    client = OpenAlexClient(
        api_key=__import__("pydantic").SecretStr("secret-key"),
        transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        cache={},
        backoff_base=0,
    )
    with pytest.raises(OpenAlexLookupError) as info:
        await client.get_by_id("W1")
    assert "secret-key" not in str(info.value)


@pytest.mark.asyncio
async def test_get_many_uses_the_ids_or_filter():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["filter"])
        return httpx.Response(200, json={"results": [WORK]})

    works = await _client(handler).get_many(["https://openalex.org/W1", "W2"])
    assert seen == ["ids.openalex:W1|W2"]
    assert works == [WORK]


def test_title_match_accepts_only_strong_matches():
    results = [
        {"display_name": "Attention Is All You Need"},
        {"display_name": "Attention Is Not All You Need"},
    ]
    assert best_title_match(["attention is all you need."], results) == results[0]
    assert best_title_match(["Transformers for vision"], results) is None
    assert title_similarity("A  Study, of Things!", "a study of things") == 1.0
