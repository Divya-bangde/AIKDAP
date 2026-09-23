"""OpenAlex Works lookups for paper identity (DOI, id, title search).

Separate from `agents.planner.paper_suggestion.OpenAlexProvider`, which
*searches for suggestions* inside the research graph; this client
resolves one known paper. Both call the same API the same way: plain
`httpx`, the optional `api_key` query parameter (OpenAlex's current
guidance: keyless works, a free key raises the daily budget 10x).

Rate limits (OpenAlex docs): at most 100 requests/second plus a daily
budget, both answered with 429. Lookups run one at a time in Celery, so
the per-second cap is never approached; a 429 is retried with
exponential backoff. Responses are cached in Redis for ~7 days, since a
work's identity and references change rarely, and the cache is
best-effort -- a Redis outage means a fresh request, never a failure.

Error messages never carry the request URL: it contains the API key.
"""

import asyncio
import json
import re
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

ENDPOINT = "https://api.openalex.org/works"
CACHE_TTL_SECONDS = 7 * 24 * 3600
_CACHE_PREFIX = "openalex:v1:"
_MAX_ATTEMPTS = 3
#: OpenAlex accepts at most 100 OR values per filter.
MAX_IDS_PER_FILTER = 100

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_DOI_TRAILING = ".,;:)]}"


class OpenAlexLookupError(Exception):
    """OpenAlex could not be reached or kept refusing the request."""


def extract_doi(text: str) -> str | None:
    """The first DOI in `text`, lowercase, trailing punctuation stripped."""
    match = _DOI_RE.search(text)
    if match is None:
        return None
    doi = match.group(0).rstrip(_DOI_TRAILING)
    # An unbalanced closing parenthesis belongs to the prose, not the DOI.
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1].rstrip(_DOI_TRAILING)
    return doi.lower()


def short_id(value: str | None) -> str | None:
    """'https://openalex.org/W123' -> 'W123'."""
    if not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1]


def parse_work(item: dict[str, Any]) -> dict[str, Any]:
    """The fields a `PaperReference` stores, from one OpenAlex work."""
    doi = item.get("doi")
    return {
        "openalex_id": short_id(item.get("id")),
        "doi": doi.removeprefix("https://doi.org/").lower() if doi else None,
        "title": item.get("display_name") or item.get("title"),
        "publication_year": item.get("publication_year"),
        "cited_by_count": int(item.get("cited_by_count") or 0),
        "referenced_works": [
            ref for ref in (short_id(value) for value in item.get("referenced_works") or []) if ref
        ],
    }


class OpenAlexClient:
    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: Any | None = None,
        backoff_base: float = 1.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.openalex_api_key
        self._timeout = timeout or settings.openalex_timeout
        # Injected only by tests (a `MockTransport`, a dict-backed cache).
        self._transport = transport
        self._cache = cache
        self._backoff_base = backoff_base

    async def get_by_doi(self, doi: str) -> dict[str, Any] | None:
        return await self._get_one(f"{ENDPOINT}/doi:{doi}")

    async def get_by_id(self, openalex_id: str) -> dict[str, Any] | None:
        return await self._get_one(f"{ENDPOINT}/{short_id(openalex_id)}")

    async def search_title(self, title: str, *, limit: int = 5) -> list[dict[str, Any]]:
        cleaned = re.sub(r"[?*:|]", " ", title).strip()
        data = await self._request(ENDPOINT, {"search": cleaned, "per_page": limit})
        return list((data or {}).get("results") or [])

    async def get_many(self, openalex_ids: list[str]) -> list[dict[str, Any]]:
        """Batch lookup by id with the `ids.openalex` OR filter."""
        works: list[dict[str, Any]] = []
        ids = [value for value in (short_id(v) for v in openalex_ids) if value]
        for start in range(0, len(ids), MAX_IDS_PER_FILTER):
            batch = ids[start : start + MAX_IDS_PER_FILTER]
            data = await self._request(
                ENDPOINT,
                {"filter": f"ids.openalex:{'|'.join(batch)}", "per_page": len(batch)},
            )
            works.extend((data or {}).get("results") or [])
        return works

    async def _get_one(self, url: str) -> dict[str, Any] | None:
        return await self._request(url, {})

    async def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET with cache and 429/5xx backoff. `None` means 404."""
        cache_key = _CACHE_PREFIX + url + "?" + json.dumps(params, sort_keys=True)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached.get("data")

        query = dict(params)
        if self._api_key is not None:
            query["api_key"] = self._api_key.get_secret_value()

        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    response = await client.get(url, params=query)
            except httpx.HTTPError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise OpenAlexLookupError(f"OpenAlex unreachable ({type(exc).__name__})") from None
                await asyncio.sleep(self._backoff_base * 2**attempt)
                continue

            if response.status_code == 404:
                await self._cache_set(cache_key, {"data": None})
                return None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise OpenAlexLookupError(f"OpenAlex returned {response.status_code}")
                await asyncio.sleep(self._backoff_base * 2**attempt)
                continue
            if response.status_code >= 400:
                raise OpenAlexLookupError(f"OpenAlex returned {response.status_code}")

            data = response.json()
            await self._cache_set(cache_key, {"data": data})
            return data
        raise OpenAlexLookupError("OpenAlex lookup failed")  # pragma: no cover - loop always returns

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        if self._cache is not None:
            return self._cache.get(key)
        try:
            import redis.asyncio as redis

            async with redis.from_url(
                settings.celery_broker_url, socket_connect_timeout=1, socket_timeout=1
            ) as client:
                raw = await client.get(key)
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001 - the cache is best-effort
            return None

    async def _cache_set(self, key: str, value: dict[str, Any]) -> None:
        if self._cache is not None:
            self._cache[key] = value
            return
        try:
            import redis.asyncio as redis

            async with redis.from_url(
                settings.celery_broker_url, socket_connect_timeout=1, socket_timeout=1
            ) as client:
                await client.set(key, json.dumps(value), ex=CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001 - the cache is best-effort
            logger.debug("openalex_cache_write_failed")
