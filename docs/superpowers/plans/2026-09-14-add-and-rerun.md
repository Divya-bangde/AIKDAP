# Add & Re-run (Milestone 10, build-order step 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user tick suggested OpenAlex papers on a completed research run, import the open-access ones as real project assets through the existing upload/processing pipeline, and — once every selected paper reaches a final state — start exactly one re-run of the original query, linked back via `parent_run_id`.

**Architecture:** A new `POST /api/v1/research/runs/{run_id}/papers/import` validates the requested ids against `run.suggested_papers` (never an arbitrary URL), flips each to `import_status="queued"` in place on that same JSONB column, and dispatches one Celery `chord`: a group of per-paper import tasks (SSRF-safe download → existing upload validators → `Asset(source=IMPORTED)` → existing extract/chunk/embed pipeline, run inline) feeding a callback that starts one new `ResearchRun` (via the existing `parent_run_id` mechanism already used for follow-ups) only if at least one paper succeeded. No new notifications module, no new migration: `research_runs.parent_run_id` already exists (added for follow-up questions), and per-paper progress is reused straight through `run.suggested_papers` plus the run's own `GET` endpoint the frontend already polls.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async), Celery (`chord`/`group`, new to this codebase), httpx (manual redirect handling for the SSRF guard), Pydantic v2, React + TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-13-persona-features-design.md`, section 3 "Add & re-run" plus its rows in sections 7 and 8.

## Global Constraints

- Step 3 ONLY — no synopsis, no build plan.
- The caller must own the run; ownership failures return 404 (`get_owned_run`, reused as-is).
- Every imported id must come from `run.suggested_papers` with a non-null `oa_pdf_url`; anything else is `422`. Never accept a client-supplied URL.
- Download: https only, connect/read timeout, streamed size cap (not post-hoc), capped+re-validated redirects, private/loopback/link-local IPs refused, content validated by PDF magic bytes.
- One paper failing does not block the re-run; all papers failing means no re-run.
- Never log or persist an unscrubbed URL or API key (`research_runs.suggested_papers[].import_error` included).
- No new notifications module (none exists in this codebase — confirmed by grep). Outcomes surface in-band: per-paper status on `run.suggested_papers`, and a `rerun_run_id`/`added_paper_count` pair computed at read-time — no new table.
- `research_runs.parent_run_id` already exists (migration `5f3c9e1a7b24`) — do not add it again. Alembic head is `c19e4b7d0a52`; this plan adds **no new migration**.
- Run `python -m graphify update .` after backend/frontend code changes (bare `graphify` is not on PATH in this environment).
- Backend commands run via `docker exec aikdap_backend <cmd>`; after touching Celery task code, `docker compose restart worker`. Frontend dev server is Docker-hosted at `:8001`'s API; `npm run generate:api` regenerates `frontend/src/types/api.d.ts` from it.
- Known environmental test failures to ignore: `test_execution_*`, `test_cross_paper`, `test_fallback_grounding`.

---

### Task 1: Settings for the paper-download guard

**Files:**
- Modify: `backend/app/core/config/settings.py` (near the existing `openalex_timeout` block, ~line 145)
- Test: `backend/tests/test_settings.py` (create if it doesn't already assert defaults elsewhere — check first; if a settings test file already exists, add to it instead)

**Interfaces:**
- Produces: `settings.paper_import_download_timeout: float` (default `30.0`), `settings.paper_import_max_redirects: int` (default `3`). Reuses the existing `settings.max_upload_size_mb` for the size cap — no new size setting.

- [ ] **Step 1: Check for an existing settings test file**

Run: `docker exec aikdap_backend python -c "import pathlib; print([p for p in pathlib.Path('tests').glob('*settings*')])"`

If one exists, add the new assertions to it. If not, create `backend/tests/test_settings.py` with just this test (do not invent a broader settings test suite — YAGNI):

```python
"""Defaults for settings this sprint added, matching the project's
existing style (Field(default=..., gt=0) for timeouts)."""

from app.core.config import settings


def test_paper_import_download_settings_have_sane_defaults():
    assert settings.paper_import_download_timeout == 30.0
    assert settings.paper_import_max_redirects == 3
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec aikdap_backend pytest tests/test_settings.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'paper_import_download_timeout'`

- [ ] **Step 3: Add the settings fields**

In `backend/app/core/config/settings.py`, immediately after the `openalex_timeout` field (the block ending `openalex_timeout: float = Field(default=20.0, gt=0)`), add:

```python
    #: Milestone 10 step 3 (Add & re-run): connect/read timeout for
    #: downloading one OpenAlex open-access PDF. Same style/naming as
    #: `openalex_timeout` above.
    paper_import_download_timeout: float = Field(default=30.0, gt=0)
    #: Maximum redirect hops `paper_import.download_oa_pdf` will follow;
    #: every hop is re-validated for scheme and host (SSRF guard), so
    #: this bounds worst-case redirect-chasing rather than trusting
    #: httpx's own follower.
    paper_import_max_redirects: int = Field(default=3, ge=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec aikdap_backend pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config/settings.py backend/tests/test_settings.py
git commit -m "$(cat <<'EOF'
feat(paper-import): add settings for the paper-download guard

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: SSRF-safe OpenAlex PDF downloader

**Files:**
- Create: `backend/app/modules/research/paper_import.py`
- Test: `backend/tests/test_paper_import_download.py`

**Interfaces:**
- Produces: `class PaperDownloadError(Exception)`, `async def download_oa_pdf(url: str, *, timeout: float, max_bytes: int, max_redirects: int, transport: httpx.AsyncBaseTransport | None = None) -> bytes`. Never raises anything except `PaperDownloadError`, and its message never contains the source URL or any query string (consumed later by Task 6, which persists the message verbatim into `run.suggested_papers[].import_error`).
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_paper_import_download.py`:

```python
"""SSRF and content-safety guards for downloading an OpenAlex
open-access PDF (Milestone 10 step 3: Add & re-run).

All network access is faked via `httpx.MockTransport` -- no live
network, matching this sprint's other httpx-based providers
(`OpenAlexProvider`'s own tests). Private-IP rejection needs no DNS
mocking: `127.0.0.1` and `localhost` both resolve to loopback on any
machine this suite runs on.
"""

import httpx
import pytest

from app.modules.research.paper_import import PaperDownloadError, download_oa_pdf

pytestmark = pytest.mark.asyncio

_PDF_BYTES = b"%PDF-1.4\n%mock pdf content\n"


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_downloads_a_valid_pdf_over_https():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PDF_BYTES)

    content = await download_oa_pdf(
        "https://example.org/paper.pdf",
        timeout=5.0,
        max_bytes=1_000_000,
        max_redirects=3,
        transport=_transport(handler),
    )
    assert content == _PDF_BYTES


async def test_rejects_plain_http():
    with pytest.raises(PaperDownloadError, match="https"):
        await download_oa_pdf(
            "http://example.org/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=3,
            transport=_transport(lambda r: httpx.Response(200, content=_PDF_BYTES)),
        )


async def test_rejects_loopback_ip_literal():
    with pytest.raises(PaperDownloadError, match="private or internal"):
        await download_oa_pdf(
            "https://127.0.0.1/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=3,
            transport=_transport(lambda r: httpx.Response(200, content=_PDF_BYTES)),
        )


async def test_rejects_localhost_hostname():
    with pytest.raises(PaperDownloadError, match="private or internal"):
        await download_oa_pdf(
            "https://localhost/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=3,
            transport=_transport(lambda r: httpx.Response(200, content=_PDF_BYTES)),
        )


async def test_rejects_oversize_content_while_streaming():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PDF_BYTES + b"x" * 100)

    with pytest.raises(PaperDownloadError, match="maximum allowed size"):
        await download_oa_pdf(
            "https://example.org/paper.pdf",
            timeout=5.0,
            max_bytes=10,
            max_redirects=3,
            transport=_transport(handler),
        )


async def test_rejects_content_that_is_not_a_pdf():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    with pytest.raises(PaperDownloadError, match="not a PDF"):
        await download_oa_pdf(
            "https://example.org/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=3,
            transport=_transport(handler),
        )


async def test_follows_a_redirect_to_another_public_https_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/paper.pdf"})
        return httpx.Response(200, content=_PDF_BYTES)

    content = await download_oa_pdf(
        "https://example.org/paper.pdf",
        timeout=5.0,
        max_bytes=1_000_000,
        max_redirects=3,
        transport=_transport(handler),
    )
    assert content == _PDF_BYTES


async def test_rejects_redirect_to_a_private_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/evil.pdf"})
        return httpx.Response(200, content=_PDF_BYTES)

    with pytest.raises(PaperDownloadError, match="private or internal"):
        await download_oa_pdf(
            "https://example.org/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=3,
            transport=_transport(handler),
        )


async def test_rejects_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.org/next.pdf"})

    with pytest.raises(PaperDownloadError, match="Too many redirects"):
        await download_oa_pdf(
            "https://example.org/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=2,
            transport=_transport(handler),
        )


async def test_rejects_http_error_status_without_leaking_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(PaperDownloadError) as exc_info:
        await download_oa_pdf(
            "https://example.org/secret-key-in-path/paper.pdf",
            timeout=5.0,
            max_bytes=1_000_000,
            max_redirects=3,
            transport=_transport(handler),
        )
    assert "secret-key-in-path" not in str(exc_info.value)
    assert "404" in str(exc_info.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend pytest tests/test_paper_import_download.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules.research.paper_import'`

- [ ] **Step 3: Implement the downloader**

Create `backend/app/modules/research/paper_import.py`:

```python
"""SSRF-safe download of one OpenAlex open-access PDF (Milestone 10
step 3: Add & re-run).

`download_oa_pdf` is the only network call this module makes on behalf
of a paper import. It never trusts the initial URL's safety to extend
to a redirect target -- `follow_redirects=False` and a manual loop mean
every hop is re-validated for scheme and host before being followed,
closing the documented SSRF technique of an initially-safe URL
redirecting to an internal address.

`PaperDownloadError` messages never include the source URL or any
query string: `app.workers.tasks` persists this message verbatim into
`research_runs.suggested_papers[].import_error`, and OpenAlex PDF URLs
occasionally carry access tokens in their path or query.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

_PDF_MAGIC = b"%PDF-"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class PaperDownloadError(Exception):
    """Raised when an open-access PDF cannot be safely downloaded."""


def _reject_if_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise PaperDownloadError("Refused to download from a private or internal address.")


def _assert_public_host(host: str) -> None:
    """Reject `host` if it is (or resolves to) a private/loopback/
    link-local address. A literal IP is checked directly; a hostname is
    resolved first and every returned address is checked -- DNS
    rebinding between the check and the request is a residual risk
    accepted here, the same as every other DNS-based host check in this
    codebase's ecosystem, given this endpoint's authenticated,
    non-anonymous callers."""
    try:
        _reject_if_private(ipaddress.ip_address(host))
        return
    except ValueError:
        pass  # not a literal IP address; resolve it below

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise PaperDownloadError("Could not resolve the download host.") from None
    for info in infos:
        _reject_if_private(ipaddress.ip_address(info[4][0]))


def _assert_safe_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise PaperDownloadError("Only https download URLs are permitted.")
    if not parts.hostname:
        raise PaperDownloadError("Download URL has no host.")
    _assert_public_host(parts.hostname)


async def download_oa_pdf(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_redirects: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    """Download and return the PDF bytes at `url`.

    Streams the response and aborts as soon as `max_bytes` is exceeded
    (never buffers an unbounded response first), and validates the
    downloaded content by its PDF magic bytes rather than trusting any
    `Content-Type` header. `transport` is injected only by tests, the
    same convention `OpenAlexProvider` uses.
    """
    current_url = url
    for _ in range(max_redirects + 1):
        _assert_safe_url(current_url)
        async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False) as client:
            try:
                async with client.stream("GET", current_url) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise PaperDownloadError("Redirect response had no location header.")
                        current_url = str(httpx.URL(current_url).join(location))
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        # `exc`'s own message embeds the full request URL
                        # (query string included) -- never propagated.
                        raise PaperDownloadError(
                            f"Download failed with HTTP {exc.response.status_code}."
                        ) from None

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise PaperDownloadError("Download exceeded the maximum allowed size.")
            except httpx.HTTPError as exc:
                if isinstance(exc, PaperDownloadError):
                    raise
                raise PaperDownloadError("Download failed due to a network error.") from None

        if not bytes(content).startswith(_PDF_MAGIC):
            raise PaperDownloadError("Downloaded content is not a PDF.")
        return bytes(content)

    raise PaperDownloadError("Too many redirects.")
```

Note: the `except httpx.HTTPError` clause above would also catch a `PaperDownloadError` raised inside the `try` block if `PaperDownloadError` subclassed `httpx.HTTPError` — it does not (it subclasses plain `Exception`), so the `isinstance` re-raise guard is defensive but harmless; keep it for clarity since `raise ... from None` inside the `try` still passes through this `except` clause's matching before Python decides `PaperDownloadError` doesn't match `httpx.HTTPError` and lets it propagate. (In practice `PaperDownloadError` never matches `httpx.HTTPError`, so this `except` only ever fires for genuine httpx network errors; the `isinstance` check is redundant safety, not required — you may remove it if `pytest` proves the plain `except httpx.HTTPError as exc: raise PaperDownloadError(...) from None` is sufficient. Verify with Step 4 either way.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec aikdap_backend pytest tests/test_paper_import_download.py -v`
Expected: PASS (10 tests). If the redundant `isinstance` guard in Step 3 causes any test to fail or you find it genuinely unreachable, simplify the `except` block to just re-raise as `PaperDownloadError` and re-run.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/research/paper_import.py backend/tests/test_paper_import_download.py
git commit -m "$(cat <<'EOF'
feat(paper-import): add SSRF-safe OpenAlex PDF downloader

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `AssetService.create_imported_asset`

**Files:**
- Modify: `backend/app/modules/assets/service.py`
- Test: `backend/tests/test_asset_service.py`

**Interfaces:**
- Consumes: `AssetService.__init__(session, storage)` (existing), `validate_extension`, `validate_extension_matches_mime`, `validate_file_size`, `validate_content_matches_mime`, `AssetValidationError` (existing, `validators.py`), `DuplicateAssetError` (existing).
- Produces: `async def create_imported_asset(self, *, owner_id: uuid.UUID, project_id: uuid.UUID, content: bytes, file_name: str, mime_type: str, title: str, asset_type: AssetType = AssetType.DOCUMENT) -> Asset`. Unlike `upload()`, does **not** enqueue `process_uploaded_asset.delay(...)` — the caller (Task 6's Celery task) runs the pipeline inline so it can observe the final `processing_status` before the import chord's callback fires. Raises `ProjectAccessDeniedError`, `AssetValidationError`, or `DuplicateAssetError`, exactly like `upload()`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_asset_service.py` (reuses the file's existing `_FakeStorage` and `session`/`project` fixtures):

```python
from app.modules.assets.validators import AssetValidationError

_PDF_BYTES = b"%PDF-1.4\nmock imported pdf\n"


async def test_create_imported_asset_persists_with_imported_source(session, project):
    service = AssetService(session, _FakeStorage())

    asset = await service.create_imported_asset(
        owner_id=project.owner_id,
        project_id=project.id,
        content=_PDF_BYTES,
        file_name="W123.pdf",
        mime_type="application/pdf",
        title="A Suggested Paper",
    )

    assert asset.source is AssetSource.IMPORTED
    assert asset.asset_type is AssetType.DOCUMENT
    assert asset.processing_status is AssetProcessingStatus.QUEUED
    assert asset.file_size == len(_PDF_BYTES)
    assert asset.title == "A Suggested Paper"


async def test_create_imported_asset_never_enqueues_processing(session, project, monkeypatch):
    """Unlike `upload()`, the caller runs the pipeline inline -- this
    method must never dispatch the Celery task itself, or the pipeline
    would run twice."""
    from app.modules.assets import service as service_module

    def _fail(*args, **kwargs):
        raise AssertionError("create_imported_asset must not enqueue process_uploaded_asset")

    monkeypatch.setattr(service_module.process_uploaded_asset, "delay", _fail)

    service = AssetService(session, _FakeStorage())
    await service.create_imported_asset(
        owner_id=project.owner_id,
        project_id=project.id,
        content=_PDF_BYTES,
        file_name="W456.pdf",
        mime_type="application/pdf",
        title="Another Paper",
    )


async def test_create_imported_asset_rejects_non_pdf_content_declared_as_pdf(session, project):
    service = AssetService(session, _FakeStorage())

    with pytest.raises(AssetValidationError):
        await service.create_imported_asset(
            owner_id=project.owner_id,
            project_id=project.id,
            content=b"<html>not a pdf</html>",
            file_name="W789.pdf",
            mime_type="application/pdf",
            title="Fake Paper",
        )


async def test_create_imported_asset_deduplicates_by_checksum(session, project):
    service = AssetService(session, _FakeStorage())
    await service.create_imported_asset(
        owner_id=project.owner_id,
        project_id=project.id,
        content=_PDF_BYTES,
        file_name="first.pdf",
        mime_type="application/pdf",
        title="First",
    )

    with pytest.raises(DuplicateAssetError):
        await service.create_imported_asset(
            owner_id=project.owner_id,
            project_id=project.id,
            content=_PDF_BYTES,
            file_name="duplicate.pdf",
            mime_type="application/pdf",
            title="Duplicate",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend pytest tests/test_asset_service.py -k create_imported_asset -v`
Expected: FAIL — `AttributeError: 'AssetService' object has no attribute 'create_imported_asset'`

- [ ] **Step 3: Implement the method**

In `backend/app/modules/assets/service.py`, add this method to `AssetService`, right after `upload()` (before `get_owned`):

```python
    async def create_imported_asset(
        self,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        content: bytes,
        file_name: str,
        mime_type: str,
        title: str,
        asset_type: AssetType = AssetType.DOCUMENT,
    ) -> Asset:
        """Create an asset from already-downloaded bytes (e.g. an
        imported OpenAlex PDF), mirroring `upload()`'s validation and
        dedup logic without the `UploadFile`-specific parts.

        Deliberately does NOT enqueue `process_uploaded_asset.delay()`:
        the caller (a paper-import Celery task) runs
        `AssetProcessingService.process_asset` inline immediately after
        this returns, so it can observe the final `processing_status`
        before the import chord's callback decides whether to start a
        re-run. Enqueuing here too would run the pipeline twice.
        """
        await self._ensure_project_owned(owner_id, project_id)

        file_name = sanitize_filename(file_name)
        extension = validate_extension(file_name)
        validate_extension_matches_mime(extension, mime_type)
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        validate_file_size(len(content), max_bytes=max_bytes)
        validate_content_matches_mime(content, mime_type)

        checksum = hashlib.sha256(content).hexdigest()
        duplicate = await self._repository.find_active_by_checksum(project_id, checksum)
        if duplicate is not None:
            raise DuplicateAssetError(duplicate)

        storage_path = await self._storage.save(
            project_id=project_id, filename=file_name, content=content
        )

        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title=title,
            description=None,
            asset_type=asset_type,
            status=AssetStatus.ACTIVE,
            mime_type=mime_type,
            file_name=file_name,
            file_extension=extension,
            file_size=len(content),
            storage_path=storage_path,
            checksum=checksum,
            source=AssetSource.IMPORTED,
            version=1,
            tags=[],
            asset_metadata={},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.QUEUED,
        )

        try:
            created = await self._repository.create(asset)
        except Exception:
            await self._storage.delete(storage_path)
            raise

        await self._session.commit()
        logger.info(
            "asset_imported",
            asset_id=str(created.id),
            project_id=str(project_id),
            owner_id=str(owner_id),
            file_name=created.file_name,
            file_size=created.file_size,
        )
        return created
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec aikdap_backend pytest tests/test_asset_service.py -v`
Expected: PASS (all, including the pre-existing dedup tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/assets/service.py backend/tests/test_asset_service.py
git commit -m "$(cat <<'EOF'
feat(paper-import): add AssetService.create_imported_asset

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `rerun_run_id` / `added_paper_count` on the run-detail response

**Files:**
- Modify: `backend/app/modules/research/repository.py`
- Modify: `backend/app/modules/research/schemas.py`
- Modify: `backend/app/modules/research/service.py`
- Modify: `backend/app/modules/research/router.py`
- Test: `backend/tests/test_research_rerun_linkage.py` (new)

**Interfaces:**
- Consumes: `ResearchRunRepository.get_by_id` (existing), `ResearchRun.parent_run_id`/`suggested_papers` (existing columns).
- Produces: `ResearchRunRepository.find_latest_child(parent_run_id) -> ResearchRun | None`; `ResearchRunDetail.rerun_run_id: uuid.UUID | None`, `ResearchRunDetail.added_paper_count: int | None`; `ResearchService.find_rerun_id(run_id) -> uuid.UUID | None`, `ResearchService.get_added_paper_count(parent_run_id) -> int | None`. Task 6 relies on `import_status` being one of `"queued" | "processing" | "added" | "failed"` inside each `suggested_papers` entry — `get_added_paper_count` counts `import_status == "added"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_research_rerun_linkage.py`:

```python
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

    assert await service.find_rerun_id(run.id) is None


async def test_find_rerun_id_returns_the_child_run(session, project):
    original = await _make_run(session, project)
    child = await _make_run(session, project, parent_run_id=original.id)

    service = ResearchService(session)
    assert await service.find_rerun_id(original.id) == child.id


async def test_find_rerun_id_returns_the_most_recent_child(session, project):
    original = await _make_run(session, project)
    await _make_run(session, project, parent_run_id=original.id)
    newest = await _make_run(session, project, parent_run_id=original.id)

    service = ResearchService(session)
    assert await service.find_rerun_id(original.id) == newest.id


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend pytest tests/test_research_rerun_linkage.py -v`
Expected: FAIL — `AttributeError: 'ResearchService' object has no attribute 'find_rerun_id'`

- [ ] **Step 3: Add the repository method**

In `backend/app/modules/research/repository.py`, add to `ResearchRunRepository` (after `create`):

```python
    async def find_latest_child(self, parent_run_id: uuid.UUID) -> ResearchRun | None:
        """Find the most recently created run linked to `parent_run_id`
        via `parent_run_id` -- used both for follow-up questions and,
        as of Milestone 10 step 3, for the re-run started after
        importing suggested papers. "Most recent" rather than "the
        one", since a run can in principle be re-run more than once."""
        stmt = (
            select(ResearchRun)
            .where(ResearchRun.parent_run_id == parent_run_id)
            .order_by(ResearchRun.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
```

- [ ] **Step 4: Add the service methods**

In `backend/app/modules/research/service.py`, add to `ResearchService` (after `get_trace`):

```python
    async def find_rerun_id(self, run_id: uuid.UUID) -> uuid.UUID | None:
        """The id of the run that resulted from importing suggested
        papers into `run_id`, if any (Milestone 10 step 3)."""
        child = await self._runs.find_latest_child(run_id)
        return child.id if child else None

    async def get_added_paper_count(self, parent_run_id: uuid.UUID) -> int | None:
        """When `parent_run_id` had papers imported into it, how many
        succeeded -- read directly off its own `suggested_papers`
        rather than a new column. `None` when that run has no
        suggested papers at all, so the frontend can distinguish "not
        a re-run" from "a re-run of zero added papers" (which cannot
        actually happen, but the type stays honest either way)."""
        parent = await self._runs.get_by_id(parent_run_id)
        if parent is None or not parent.suggested_papers:
            return None
        return sum(1 for paper in parent.suggested_papers if paper.get("import_status") == "added")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec aikdap_backend pytest tests/test_research_rerun_linkage.py -v`
Expected: PASS

- [ ] **Step 6: Wire the fields into `ResearchRunDetail` and the router**

In `backend/app/modules/research/schemas.py`, modify `ResearchRunDetail` (the class starting at line 417):

```python
class ResearchRunDetail(ResearchRunRead):
    """A run together with its full Explainable-AI trace."""

    steps: list[ResearchStepRead] = Field(default_factory=list)
    messages: list[AgentMessageRead] = Field(default_factory=list)
    #: Verified claims, split out of `citations` (see `from_model`) so a
    #: client never has to distinguish claim entries from ordinary
    #: citation entries itself.
    claims: list[VerifiedClaimRead] = Field(default_factory=list)
    #: The id of the run started by importing this run's suggested
    #: papers (Milestone 10 step 3: Add & re-run), if any.
    rerun_run_id: uuid.UUID | None = None
    #: When THIS run is itself such a re-run (`parent_run_id` set), how
    #: many papers were successfully added to the parent before it
    #: started.
    added_paper_count: int | None = None

    @classmethod
    def from_model(
        cls,
        run: ResearchRun,
        *,
        steps: list[ResearchStepRead],
        messages: list[AgentMessageRead],
        rerun_run_id: uuid.UUID | None = None,
        added_paper_count: int | None = None,
    ) -> "ResearchRunDetail":
        """Build the detail response, splitting claims out of `citations`.

        `run.citations` is one JSONB array carrying both ordinary
        citation dicts and claim dicts (`kind="claim"`) -- see
        `nodes.synthesis_node`. This is the one place that split
        happens, so every other reader of `ResearchRunDetail.citations`
        keeps seeing exactly the citation shape it always has.
        """
        base = ResearchRunRead.model_validate(run)
        raw = base.citations or []
        claims = [VerifiedClaimRead.model_validate(item) for item in raw if item.get("kind") == "claim"]
        citations = [item for item in raw if item.get("kind") != "claim"]
        return cls(
            **{**base.model_dump(), "citations": citations},
            steps=steps,
            messages=messages,
            claims=claims,
            rerun_run_id=rerun_run_id,
            added_paper_count=added_paper_count,
        )
```

In `backend/app/modules/research/router.py`, modify `get_research_run`:

```python
@router.get("/runs/{run_id}", response_model=ResearchRunDetail)
async def get_research_run(
    run: ResearchRun = Depends(get_owned_run),
    service: ResearchService = Depends(get_research_service),
) -> ResearchRunDetail:
    """Fetch one run with its full execution trace and agent transcript."""
    steps, messages = await service.get_trace(run)
    rerun_run_id = await service.find_rerun_id(run.id)
    added_paper_count = (
        await service.get_added_paper_count(run.parent_run_id)
        if run.parent_run_id is not None
        else None
    )
    return ResearchRunDetail.from_model(
        run,
        steps=[ResearchStepRead.model_validate(step) for step in steps],
        # `AgentMessage` needs the explicit bridge from its
        # `message_metadata` attribute to the `metadata` API field.
        messages=[AgentMessageRead.from_model(message) for message in messages],
        rerun_run_id=rerun_run_id,
        added_paper_count=added_paper_count,
    )
```

- [ ] **Step 7: Run the full research test suite to check nothing regressed**

Run: `docker exec aikdap_backend pytest tests/test_research_rerun_linkage.py tests/test_research_citations.py tests/test_research_web_and_visuals.py tests/test_research_equations.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/modules/research/repository.py backend/app/modules/research/schemas.py backend/app/modules/research/service.py backend/app/modules/research/router.py backend/tests/test_research_rerun_linkage.py
git commit -m "$(cat <<'EOF'
feat(paper-import): expose rerun_run_id/added_paper_count on run detail

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Celery import chord — per-paper task and finalize callback

**Files:**
- Modify: `backend/app/workers/tasks.py`
- Test: `backend/tests/test_paper_import_tasks.py` (new)

**Interfaces:**
- Consumes: `download_oa_pdf`/`PaperDownloadError` (Task 2), `AssetService.create_imported_asset` (Task 3), `AssetProcessingService`/`get_asset_processing_service` (existing `pipeline.py`), `ResearchRunRepository` (existing + Task 4's `find_latest_child`, unused here but same module), `execute_research_run` (existing task in this same file).
- Produces:
  - `async def _import_paper(run_id: uuid.UUID, paper: dict[str, Any]) -> dict[str, Any]` — returns `{"openalex_id": str, "status": "added" | "failed", "asset_id": str | None, "reason": str | None}`. Never raises for a paper-specific failure.
  - `import_suggested_paper` — Celery task wrapping `_import_paper`, `name="workers.import_suggested_paper"`.
  - `async def _finalize_import(results: list[dict[str, Any]], run_id: uuid.UUID) -> dict[str, Any]` — creates and dispatches exactly one new `ResearchRun` (via `parent_run_id`) if any paper's `status == "added"`, else does nothing.
  - `finalize_paper_import` — Celery task wrapping `_finalize_import`, `name="workers.finalize_paper_import"`.
  - `def dispatch_paper_import(run_id: str, papers: list[dict[str, Any]]) -> None` — builds and fires the `chord(group(...))(...)`. This is what Task 6's `ResearchService.import_papers` calls, so `research/service.py` never imports Celery's `chord`/`group` directly (matching the existing convention that Celery specifics live in `workers/tasks.py`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_paper_import_tasks.py`:

```python
"""Milestone 10 step 3 (Add & re-run): the per-paper import task and
the chord's finalize callback, called as plain async functions (this
codebase's established convention -- see `test_research_citations.py`
calling `ResearchExecutionService(session).execute(...)` directly
rather than through Celery's eager mode). No live network: the
downloader is monkeypatched at the module level `tasks.py` imports it
from.
"""

import uuid

import pytest

from app.modules.assets.enums import AssetProcessingStatus, AssetSource
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import StorageProvider
from app.modules.knowledge_base.embeddings import NullEmbeddingProvider
from app.modules.assets.processing.document_understanding import (
    DocumentUnderstandingError,
    QwenDocumentUnderstandingService,
)
from app.modules.assets.processing.pipeline import AssetProcessingService
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
from app.modules.research.paper_import import PaperDownloadError
from app.modules.research.repository import ResearchRunRepository
from app.workers import tasks as tasks_module

pytestmark = pytest.mark.asyncio

_PDF_BYTES = b"%PDF-1.4\nmock imported pdf\n"


class _FakeStorage(StorageProvider):
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def save(self, *, project_id, filename, content: bytes) -> str:
        path = f"{project_id}/{filename}"
        self._files[path] = content
        return path

    async def read(self, storage_path: str) -> bytes:
        return self._files[storage_path]

    async def delete(self, storage_path: str) -> None:
        self._files.pop(storage_path, None)

    def exists(self, storage_path: str) -> bool:
        return storage_path in self._files


class _NoOpUnderstanding(QwenDocumentUnderstandingService):
    def __init__(self) -> None:
        pass

    async def analyze(self, text: str):
        raise DocumentUnderstandingError("skipped in this test")


def _fake_processing_service(session, storage):
    """Real extract -> chunk -> embed, with Qwen/embeddings faked out --
    same convention as `test_asset_processing_pipeline.py`."""
    return AssetProcessingService(
        session,
        storage,
        chunk_size=500,
        chunk_overlap=50,
        understanding=_NoOpUnderstanding(),
        embeddings=NullEmbeddingProvider(),
    )


def _paper(**overrides) -> dict:
    return {
        "openalex_id": "https://openalex.org/W1",
        "title": "A Suggested Paper",
        "oa_pdf_url": "https://example.org/paper.pdf",
        **overrides,
    }


async def _make_run(session, project, **overrides) -> ResearchRun:
    run = ResearchRun(
        project_id=project.id,
        owner_id=project.owner_id,
        query=overrides.pop("query", "A research question?"),
        status=ResearchRunStatus.COMPLETED,
        include_assets=True,
        include_web=True,
        max_results=5,
        **overrides,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


@pytest.fixture(autouse=True)
def _fakes(session, monkeypatch):
    storage = _FakeStorage()
    monkeypatch.setattr(tasks_module, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(
        tasks_module, "get_asset_processing_service", _fake_processing_service
    )
    monkeypatch.setattr(
        tasks_module, "async_session_factory", lambda: _SameSessionContext(session)
    )
    return storage


class _SameSessionContext:
    """Test double for `async_session_factory()`'s `async with` usage --
    hands back the shared test `session` instead of opening a real new
    one, so every task call in a test sees the same transactional state
    the test itself set up (matching `test_asset_processing_pipeline.py`
    convention adapted to `async with`)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


async def test_import_paper_downloads_validates_processes_and_marks_added(
    session, project, monkeypatch
):
    async def fake_download(url, **kwargs):
        assert url == "https://example.org/paper.pdf"
        return _PDF_BYTES

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)

    run = await _make_run(
        session, project, suggested_papers=[_paper()], parent_run_id=None
    )

    result = await tasks_module._import_paper(run.id, _paper())

    assert result["status"] == "added"
    assert result["asset_id"] is not None

    asset = await AssetRepository(session).get_by_id(uuid.UUID(result["asset_id"]))
    assert asset.source is AssetSource.IMPORTED
    assert asset.processing_status is AssetProcessingStatus.COMPLETED

    await session.refresh(run)
    entry = run.suggested_papers[0]
    assert entry["import_status"] == "added"
    assert entry["imported_asset_id"] == result["asset_id"]


async def test_import_paper_marks_failed_on_download_error(session, project, monkeypatch):
    async def fake_download(url, **kwargs):
        raise PaperDownloadError("Downloaded content is not a PDF.")

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)

    run = await _make_run(session, project, suggested_papers=[_paper()])

    result = await tasks_module._import_paper(run.id, _paper())

    assert result["status"] == "failed"
    assert result["reason"] == "Downloaded content is not a PDF."

    await session.refresh(run)
    assert run.suggested_papers[0]["import_status"] == "failed"
    assert run.suggested_papers[0]["import_error"] == "Downloaded content is not a PDF."


async def test_import_paper_sets_processing_status_before_downloading(
    session, project, monkeypatch
):
    """The per-paper status must reach `processing` even if the download
    itself later fails -- a card frozen on `queued` forever would look
    hung."""
    seen_mid_download: dict = {}

    async def fake_download(url, **kwargs):
        run = await ResearchRunRepository(session).get_by_id(run_id_holder["id"])
        seen_mid_download["status"] = run.suggested_papers[0]["import_status"]
        raise PaperDownloadError("boom")

    monkeypatch.setattr(tasks_module, "download_oa_pdf", fake_download)

    run = await _make_run(session, project, suggested_papers=[_paper()])
    run_id_holder = {"id": run.id}

    await tasks_module._import_paper(run.id, _paper())

    assert seen_mid_download["status"] == "processing"


async def test_finalize_import_starts_exactly_one_rerun_on_partial_success(
    session, project, monkeypatch
):
    dispatched: list[str] = []
    monkeypatch.setattr(
        tasks_module.execute_research_run, "delay", lambda run_id: dispatched.append(run_id)
    )

    original = await _make_run(
        session,
        project,
        query="Original question?",
        suggested_papers=[
            {**_paper(openalex_id="W1"), "import_status": "added"},
            {**_paper(openalex_id="W2"), "import_status": "failed"},
        ],
    )
    results = [
        {"openalex_id": "W1", "status": "added", "asset_id": "irrelevant", "reason": None},
        {"openalex_id": "W2", "status": "failed", "asset_id": None, "reason": "not a PDF"},
    ]

    outcome = await tasks_module._finalize_import(results, original.id)

    assert outcome["status"] == "rerun_started"
    assert len(dispatched) == 1

    child = await ResearchRunRepository(session).find_latest_child(original.id)
    assert child is not None
    assert child.query == "Original question?"
    assert child.parent_run_id == original.id
    assert str(child.id) == dispatched[0]


async def test_finalize_import_starts_no_rerun_when_every_paper_failed(
    session, project, monkeypatch
):
    dispatched: list[str] = []
    monkeypatch.setattr(
        tasks_module.execute_research_run, "delay", lambda run_id: dispatched.append(run_id)
    )

    original = await _make_run(session, project)
    results = [
        {"openalex_id": "W1", "status": "failed", "asset_id": None, "reason": "not a PDF"},
        {"openalex_id": "W2", "status": "failed", "asset_id": None, "reason": "too large"},
    ]

    outcome = await tasks_module._finalize_import(results, original.id)

    assert outcome["status"] == "all_failed"
    assert dispatched == []
    assert await ResearchRunRepository(session).find_latest_child(original.id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend pytest tests/test_paper_import_tasks.py -v`
Expected: FAIL — `AttributeError: module 'app.workers.tasks' has no attribute '_import_paper'`

- [ ] **Step 3: Implement the tasks**

In `backend/app/workers/tasks.py`, add these imports near the top (alongside the existing `app.modules.research.service` import):

```python
from celery import chord, group

from app.modules.assets.service import AssetService, DuplicateAssetError
from app.modules.assets.validators import AssetValidationError
from app.modules.research.paper_import import PaperDownloadError, download_oa_pdf
from app.modules.research.repository import ResearchRunRepository
```

Then append, after `_run_research` and before `recover_execution_job_retry`:

```python
# ---------------------------------------------------------------------------
# Milestone 10 step 3: Add & re-run
#
# One `import_suggested_paper` task per selected paper (download ->
# existing upload validators -> Asset(source=IMPORTED) -> existing
# extract/chunk/embed pipeline, run inline so this task can observe the
# final `processing_status`), fed into a `chord` whose callback
# (`finalize_paper_import`) starts exactly one re-run once every paper
# has reached a final state -- and only if at least one succeeded.
# ---------------------------------------------------------------------------


def dispatch_paper_import(run_id: str, papers: list[dict[str, Any]]) -> None:
    """Fire the import chord for one run's selected papers.

    The only place `celery.chord`/`celery.group` are constructed --
    `ResearchService.import_papers` calls this rather than importing
    Celery primitives itself, the same separation `start_run` keeps by
    importing `execute_research_run` locally instead of calling
    `.delay()` on something it constructs.
    """
    chord(group(import_suggested_paper.s(run_id, paper) for paper in papers))(
        finalize_paper_import.s(run_id)
    )


@celery_app.task(name="workers.import_suggested_paper", bind=True, max_retries=0)
@log_task_execution
def import_suggested_paper(self, run_id: str, paper: dict[str, Any]) -> dict[str, Any]:
    """Download, validate, and process one OpenAlex-suggested paper as a
    project asset.

    Never retries and never raises for a paper-specific failure (bad
    link, not a PDF, too large, extraction failed): each is a normal
    outcome for one paper among several selected, recorded on
    `run.suggested_papers[].import_status` and returned so the chord's
    callback can still start a re-run with the papers that succeeded.
    """
    return _run_task_loop(_import_paper(uuid.UUID(run_id), paper))


async def _import_paper(run_id: uuid.UUID, paper: dict[str, Any]) -> dict[str, Any]:
    openalex_id = paper["openalex_id"]
    async with async_session_factory() as session:
        await _update_paper_import_status(session, run_id, openalex_id, status="processing")

        try:
            content = await download_oa_pdf(
                paper["oa_pdf_url"],
                timeout=settings.paper_import_download_timeout,
                max_bytes=settings.max_upload_size_mb * 1024 * 1024,
                max_redirects=settings.paper_import_max_redirects,
            )
        except PaperDownloadError as exc:
            await _update_paper_import_status(
                session, run_id, openalex_id, status="failed", error=str(exc)
            )
            return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": str(exc)}

        run = await ResearchRunRepository(session).get_by_id(run_id)
        if run is None:
            reason = "The research run no longer exists."
            await _update_paper_import_status(session, run_id, openalex_id, status="failed", error=reason)
            return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": reason}

        storage = get_storage_provider()
        file_name = f"{openalex_id.rsplit('/', 1)[-1]}.pdf"
        try:
            asset = await AssetService(session, storage).create_imported_asset(
                owner_id=run.owner_id,
                project_id=run.project_id,
                content=content,
                file_name=file_name,
                mime_type="application/pdf",
                title=paper.get("title") or file_name,
            )
        except (AssetValidationError, DuplicateAssetError) as exc:
            await _update_paper_import_status(
                session, run_id, openalex_id, status="failed", error=str(exc)
            )
            return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": str(exc)}

        await get_asset_processing_service(session, storage).process_asset(asset.id)
        await session.refresh(asset)

        if asset.processing_status is AssetProcessingStatus.COMPLETED:
            await _update_paper_import_status(
                session, run_id, openalex_id, status="added", asset_id=str(asset.id)
            )
            return {"openalex_id": openalex_id, "status": "added", "asset_id": str(asset.id), "reason": None}

        reason = asset.processing_error or f"Processing ended in status '{asset.processing_status.value}'."
        await _update_paper_import_status(session, run_id, openalex_id, status="failed", error=reason)
        return {"openalex_id": openalex_id, "status": "failed", "asset_id": None, "reason": reason}


async def _update_paper_import_status(
    session,
    run_id: uuid.UUID,
    openalex_id: str,
    *,
    status: str,
    asset_id: str | None = None,
    error: str | None = None,
) -> None:
    """Mutate one entry of `run.suggested_papers` in place.

    JSONB mutation needs a whole-list reassignment for SQLAlchemy's
    change tracking to see it (mutating a nested dict in place would be
    silently lost) -- so this rebuilds the list rather than editing the
    matched entry's dict directly.
    """
    run = await ResearchRunRepository(session).get_by_id(run_id)
    if run is None or not run.suggested_papers:
        return
    updated = []
    for entry in run.suggested_papers:
        if entry.get("openalex_id") == openalex_id:
            entry = {**entry, "import_status": status}
            if asset_id is not None:
                entry["imported_asset_id"] = asset_id
            if error is not None:
                entry["import_error"] = error
        updated.append(entry)
    run.suggested_papers = updated
    await session.commit()


@celery_app.task(name="workers.finalize_paper_import", bind=True, max_retries=0)
@log_task_execution
def finalize_paper_import(self, results: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Chord callback: once every selected paper has reached a final
    state, start exactly one re-run with the original query -- only if
    at least one paper was added."""
    return _run_task_loop(_finalize_import(results, uuid.UUID(run_id)))


async def _finalize_import(results: list[dict[str, Any]], run_id: uuid.UUID) -> dict[str, Any]:
    added = [result for result in results if result.get("status") == "added"]
    if not added:
        logger.info("paper_import_all_failed", run_id=str(run_id), paper_count=len(results))
        return {"status": "all_failed", "run_id": str(run_id)}

    async with async_session_factory() as session:
        runs = ResearchRunRepository(session)
        run = await runs.get_by_id(run_id)
        if run is None:
            logger.error("paper_import_source_run_missing", run_id=str(run_id))
            return {"status": "source_run_missing", "run_id": str(run_id)}

        new_run = ResearchRun(
            project_id=run.project_id,
            owner_id=run.owner_id,
            task_id=run.task_id,
            parent_run_id=run.id,
            query=run.query,
            status=ResearchRunStatus.PENDING,
            include_assets=True,
            include_web=run.include_web,
            max_results=run.max_results,
        )
        created = await runs.create(new_run)
        await session.commit()
        new_run_id = created.id

    execute_research_run.delay(str(new_run_id))
    logger.info(
        "paper_import_rerun_started",
        source_run_id=str(run_id),
        rerun_id=str(new_run_id),
        added_paper_count=len(added),
    )
    return {"status": "rerun_started", "run_id": str(run_id), "rerun_id": str(new_run_id)}
```

This also needs two more imports already-used-elsewhere-in-file names available at module scope: `ResearchRun` and `ResearchRunStatus`. Add:

```python
from app.modules.research.enums import ResearchRunStatus
from app.modules.research.models import ResearchRun
```

(`AssetProcessingStatus` is already imported at the top of `tasks.py`; reuse it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec aikdap_backend pytest tests/test_paper_import_tasks.py -v`
Expected: PASS. If `_SameSessionContext`/monkeypatching `async_session_factory` proves awkward against the real module-level `async_session_factory` import inside `tasks.py` (it's imported by name at module load, so `monkeypatch.setattr(tasks_module, "async_session_factory", ...)` must target the name as used inside `tasks.py`, not `app.database.session.async_session_factory` — confirm which one `_import_paper`/`_finalize_import` actually call at runtime and monkeypatch that reference), adjust the fixture accordingly and re-run.

- [ ] **Step 5: Restart the worker and commit**

```bash
docker compose restart worker
```

```bash
git add backend/app/workers/tasks.py backend/tests/test_paper_import_tasks.py
git commit -m "$(cat <<'EOF'
feat(paper-import): add per-paper import task and re-run chord callback

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `POST /research/runs/{run_id}/papers/import`

**Files:**
- Modify: `backend/app/modules/research/schemas.py`
- Modify: `backend/app/modules/research/service.py`
- Modify: `backend/app/modules/research/router.py`
- Test: `backend/tests/test_paper_import_router.py` (new)

**Interfaces:**
- Consumes: `dispatch_paper_import` (Task 5), `get_owned_run` (existing dependency — ownership/404), `ResearchRun.suggested_papers` (existing).
- Produces: `PaperImportRequest(openalex_ids: list[str])`, `PaperImportStatus(openalex_id: str, status: Literal["queued"])`, `PaperImportAccepted(run_id: uuid.UUID, papers: list[PaperImportStatus])`; `ResearchService.import_papers(run, openalex_ids) -> list[dict]`; `UnknownSuggestedPaperError`, `PaperNotOpenAccessError` (both -> 422); route `POST /research/runs/{run_id}/papers/import` -> `202`.

This codebase has no HTTP-level (`TestClient`) test precedent anywhere (confirmed: only one file even mentions `TestClient`, and its own docstring says "there is no existing precedent for HTTP-level testing"). Ownership and validation are tested at the service layer, matching every other router in this codebase — the router itself is a thin, uniform exception-to-`HTTPException` translator, same pattern as `_raise_validation` elsewhere in `research/router.py`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_paper_import_router.py`:

```python
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
    from app.modules.research import service as service_module

    dispatched: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(
        service_module,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec aikdap_backend pytest tests/test_paper_import_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'UnknownSuggestedPaperError' from 'app.modules.research.service'`

- [ ] **Step 3: Add the schemas**

In `backend/app/modules/research/schemas.py`, add near `ResearchRunAccepted`:

```python
class PaperImportRequest(BaseModel):
    """Payload for importing selected OpenAlex-suggested papers."""

    openalex_ids: list[str] = Field(min_length=1)


class PaperImportStatus(BaseModel):
    """One paper's status immediately after the import request is accepted."""

    openalex_id: str
    status: Literal["queued"]


class PaperImportAccepted(BaseModel):
    """Immediate `202` response: every requested paper has been validated
    and queued. Poll `GET /research/runs/{run_id}` -- each paper's
    entry in `suggested_papers` reports `import_status` as it
    progresses (`queued` -> `processing` -> `added`/`failed`), and
    `rerun_run_id` appears once the linked re-run starts.
    """

    run_id: uuid.UUID
    papers: list[PaperImportStatus]
```

- [ ] **Step 4: Add the service method and exceptions**

In `backend/app/modules/research/service.py`, add near the other exception classes (after `UnsourcedSynthesisFailedError`):

```python
class UnknownSuggestedPaperError(Exception):
    """Raised when a requested id is not one of the run's suggested papers."""


class PaperNotOpenAccessError(Exception):
    """Raised when a requested paper has no open-access PDF to import."""
```

Add the import (function-scoped, same reasoning as `execute_research_run`'s import in `start_run` — `app.workers.tasks` imports `ResearchExecutionService` from this module, so a top-level import of anything from `app.workers.tasks` here would be circular):

Add to `ResearchService`, after `create_unsourced_run`:

```python
    async def import_papers(self, run: ResearchRun, openalex_ids: list[str]) -> list[dict[str, Any]]:
        """Validate the requested papers and dispatch the import chord.

        `run` is already ownership-checked by the caller (`get_owned_run`
        resolved it from the path), matching `create_unsourced_run`'s
        contract above. Every id must belong to `run.suggested_papers`
        and carry a non-null `oa_pdf_url` -- never an arbitrary
        client-supplied URL (Milestone 10 step 3).
        """
        available = {paper["openalex_id"]: paper for paper in run.suggested_papers or []}
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for openalex_id in openalex_ids:
            if openalex_id in seen:
                continue
            seen.add(openalex_id)
            paper = available.get(openalex_id)
            if paper is None:
                raise UnknownSuggestedPaperError(openalex_id)
            if not paper.get("oa_pdf_url"):
                raise PaperNotOpenAccessError(openalex_id)
            selected.append(paper)

        updated = []
        for entry in run.suggested_papers:
            if entry["openalex_id"] in seen:
                entry = {**entry, "import_status": "queued"}
            updated.append(entry)
        run.suggested_papers = updated
        await self._session.commit()

        logger.info(
            "paper_import_dispatched",
            run_id=str(run.id),
            openalex_ids=sorted(seen),
        )

        from app.workers.tasks import dispatch_paper_import

        dispatch_paper_import(str(run.id), selected)

        return [{"openalex_id": paper["openalex_id"], "status": "queued"} for paper in selected]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec aikdap_backend pytest tests/test_paper_import_router.py -v`
Expected: PASS

- [ ] **Step 6: Add the router endpoint**

In `backend/app/modules/research/router.py`, add the imports:

```python
from app.modules.research.schemas import (
    ...  # existing list
    PaperImportAccepted,
    PaperImportRequest,
)
from app.modules.research.service import (
    ...  # existing list
    PaperNotOpenAccessError,
    UnknownSuggestedPaperError,
)
```

Then add the route, right after `create_unsourced_run_route`:

```python
@router.post(
    "/runs/{run_id}/papers/import",
    response_model=PaperImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_suggested_papers_route(
    data: PaperImportRequest,
    run: ResearchRun = Depends(get_owned_run),
    service: ResearchService = Depends(get_research_service),
) -> PaperImportAccepted:
    """Import selected OpenAlex-suggested papers as project assets and,
    once every one reaches a final state, start exactly one re-run
    (Milestone 10 step 3: Add & re-run)."""
    try:
        papers = await service.import_papers(run, data.openalex_ids)
    except UnknownSuggestedPaperError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{exc}' is not a suggested paper for this run.",
        ) from exc
    except PaperNotOpenAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{exc}' has no open-access PDF to import.",
        ) from exc
    return PaperImportAccepted(run_id=run.id, papers=papers)
```

- [ ] **Step 7: Confirm the app still imports cleanly**

Run: `docker exec aikdap_backend python -c "from app.main import app"`
Expected: no output, exit code 0 (this catches any import cycle or route-registration error the unit tests above wouldn't).

- [ ] **Step 8: Commit**

```bash
git add backend/app/modules/research/schemas.py backend/app/modules/research/service.py backend/app/modules/research/router.py backend/tests/test_paper_import_router.py
git commit -m "$(cat <<'EOF'
feat(paper-import): add POST /research/runs/{run_id}/papers/import

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Regenerate frontend API types

**Files:**
- Modify (generated): `frontend/src/types/api.d.ts`

**Interfaces:**
- Produces: `components["schemas"]["PaperImportRequest"]`, `["PaperImportAccepted"]`, `["PaperImportStatus"]`; `ResearchRunDetail` gains `rerun_run_id` and `added_paper_count`.

- [ ] **Step 1: Ensure the backend is running with this branch's code**

```bash
docker compose up -d --force-recreate --no-deps backend worker
```

- [ ] **Step 2: Regenerate**

```bash
cd frontend && npm run generate:api
```

- [ ] **Step 3: Verify the new types landed**

Run: `grep -c "PaperImportRequest\|rerun_run_id" frontend/src/types/api.d.ts`
Expected: a non-zero count.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/api.d.ts
git commit -m "$(cat <<'EOF'
chore(paper-import): regenerate frontend API types

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Frontend service call + status vocabulary

**Files:**
- Modify: `frontend/src/services/research.ts`
- Modify: `frontend/src/lib/status.ts`
- Test: `frontend/src/lib/status.test.ts` (create only if no such file exists — check first; otherwise skip a dedicated test, since `status.ts` has no existing per-domain unit tests to extend, per YAGNI, and the domain is exercised end-to-end by Task 9's component tests)

**Interfaces:**
- Produces: `importSuggestedPapers(runId: string, openalexIds: string[]): Promise<PaperImportAccepted>`; `statusBadge("paperImport", value)` for `queued | processing | added | failed`.

- [ ] **Step 1: Check whether `status.ts` already has a test file**

Run: `ls frontend/src/lib/*.test.ts 2>&1 | grep status || echo none`

If `status.test.ts` exists, skip adding a new test file for this task (add nothing — YAGNI, the domain map is exercised through `PaperSuggestionsPanel.test.tsx` in Task 9). If genuinely none exists project-wide for `status.ts` and you judge one now needed for the docstring's stated verification appetite, prefer relying on Task 9's component tests, which exercise every added domain value through rendered output — do not create a new low-value unit-test file just for a lookup table.

- [ ] **Step 2: Add the service function**

In `frontend/src/services/research.ts`, add the two type aliases near the top and the function at the end:

```typescript
type PaperImportRequest = { openalex_ids: string[] };
type PaperImportAccepted = components["schemas"]["PaperImportAccepted"];
```

```typescript
export function importSuggestedPapers(runId: string, openalexIds: string[]) {
  return request<PaperImportAccepted>(`/api/v1/research/runs/${runId}/papers/import`, {
    method: "POST",
    body: { openalex_ids: openalexIds } satisfies PaperImportRequest,
  });
}
```

- [ ] **Step 3: Add the `paperImport` status domain**

In `frontend/src/lib/status.ts`, add to the `MAPS` object (after `researchStep`):

```typescript
  // Milestone 10 step 3 (Add & re-run): per-card status on
  // `PaperSuggestionsPanel` while a selected paper is imported.
  paperImport: {
    queued: badge("muted", "Queued"),
    processing: badge("secondary", "Processing"),
    added: badge("success", "Added"),
    failed: badge("destructive", "Failed"),
  },
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && npm run typecheck` (or `npx tsc --noEmit` if there is no dedicated script — check `package.json` first)

Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/research.ts frontend/src/lib/status.ts
git commit -m "$(cat <<'EOF'
feat(paper-import): add importSuggestedPapers service call and status vocabulary

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `PaperSuggestionsPanel` — checkboxes, Add & re-run, per-card status, re-run link

**Files:**
- Modify: `frontend/src/features/research/PaperSuggestionsPanel.tsx`
- Modify: `frontend/src/features/research/PaperSuggestionsPanel.test.tsx`

**Interfaces:**
- Consumes: `importSuggestedPapers` (Task 8), `statusBadge("paperImport", ...)` via `StatusBadge` (existing), `messageFor` (existing, `@/lib/api-error`), `Button` (existing shadcn component).
- Produces: `PaperSuggestionsPanel` gains required props `runId: string` and `onImported?: () => void` (called after a successful import request, so the parent can invalidate/refetch the run — mirrors `FollowUpPrompt`'s `onSuccess` pattern but as a prop, since the polling parent owns the query, not this panel). `SuggestedPaper` gains optional fields already produced by the backend's `suggested_papers` JSONB: `import_status?: "queued" | "processing" | "added" | "failed"`, `imported_asset_id?: string | null`, `import_error?: string | null`.

- [ ] **Step 1: Write the failing tests**

Replace `frontend/src/features/research/PaperSuggestionsPanel.test.tsx` with (existing tests kept, new ones added):

```tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PaperSuggestionsPanel, type SuggestedPaper } from "@/features/research/PaperSuggestionsPanel";
import * as researchService from "@/services/research";
import { renderWithProviders } from "@/test/render";

function makePaper(overrides: Partial<SuggestedPaper> = {}): SuggestedPaper {
  return {
    openalex_id: "https://openalex.org/W1",
    title: "Deep Learning for Poultry Disease Detection",
    authors: ["A. Researcher", "B. Scientist"],
    year: 2023,
    cited_by_count: 42,
    landing_url: "https://example.org/paper",
    oa_pdf_url: null,
    relevance_note: "Suggested to help address: missing baseline comparison.",
    ...overrides,
  };
}

describe("PaperSuggestionsPanel", () => {
  it("renders nothing when there are no papers", () => {
    const { container } = renderWithProviders(<PaperSuggestionsPanel papers={[]} runId="run-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a card per paper, with the publisher-link fallback when there is no OA PDF", () => {
    renderWithProviders(<PaperSuggestionsPanel papers={[makePaper()]} runId="run-1" />);

    expect(screen.getByText("Deep Learning for Poultry Disease Detection")).toBeInTheDocument();
    expect(screen.getByText(/A\. Researcher, B\. Scientist/)).toBeInTheDocument();
    expect(screen.getByText(/2023/)).toBeInTheDocument();
    expect(screen.getByText(/42 citations/)).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "Open on publisher site" });
    expect(link).toHaveAttribute("href", "https://example.org/paper");
  });

  it("links to the open-access PDF when one is available", () => {
    renderWithProviders(
      <PaperSuggestionsPanel papers={[makePaper({ oa_pdf_url: "https://example.org/paper.pdf" })]} runId="run-1" />,
    );

    const link = screen.getByRole("link", { name: "Open PDF" });
    expect(link).toHaveAttribute("href", "https://example.org/paper.pdf");
  });

  it("renders one card per paper when there are several", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", title: "First paper" }), makePaper({ openalex_id: "W2", title: "Second paper" })]}
        runId="run-1"
      />,
    );

    expect(screen.getByText("First paper")).toBeInTheDocument();
    expect(screen.getByText("Second paper")).toBeInTheDocument();
  });

  it("gives a paper with an OA PDF a checkbox, and a paywalled paper none", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[
          makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf" }),
          makePaper({ openalex_id: "W2", oa_pdf_url: null }),
        ]}
        runId="run-1"
      />,
    );

    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  });

  it("keeps the Add & re-run button disabled until a paper is selected", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf" })]}
        runId="run-1"
      />,
    );

    const button = screen.getByRole("button", { name: /add & re-run/i });
    expect(button).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
  });

  it("calls importSuggestedPapers with the selected ids and notifies the parent on success", async () => {
    const importSpy = vi
      .spyOn(researchService, "importSuggestedPapers")
      .mockResolvedValue({ run_id: "run-1", papers: [{ openalex_id: "W1", status: "queued" }] });
    const onImported = vi.fn();

    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf" })]}
        runId="run-1"
        onImported={onImported}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /add & re-run/i }));

    await waitFor(() => expect(importSpy).toHaveBeenCalledWith("run-1", ["W1"]));
    await waitFor(() => expect(onImported).toHaveBeenCalled());
  });

  it("renders each card's import status once present", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[
          makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf", import_status: "processing" }),
          makePaper({
            openalex_id: "W2",
            oa_pdf_url: "https://example.org/b.pdf",
            import_status: "failed",
            import_error: "Downloaded content is not a PDF.",
          }),
        ]}
        runId="run-1"
      />,
    );

    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Downloaded content is not a PDF.")).toBeInTheDocument();
  });

  it("links to the re-run once it exists", () => {
    renderWithProviders(
      <PaperSuggestionsPanel
        papers={[
          makePaper({ openalex_id: "W1", oa_pdf_url: "https://example.org/a.pdf", import_status: "added" }),
        ]}
        runId="run-1"
        rerunRunId="run-2"
      />,
    );

    const link = screen.getByRole("link", { name: /view re-run/i });
    expect(link).toHaveAttribute("href", "/research/run-2");
  });
});
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd frontend && npx vitest run src/features/research/PaperSuggestionsPanel.test.tsx`
Expected: FAIL — the new tests (checkbox count, disabled button, etc.) fail against the current read-only component; the four pre-existing tests still pass.

- [ ] **Step 3: Implement the component**

Replace `frontend/src/features/research/PaperSuggestionsPanel.tsx`:

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { messageFor } from "@/lib/api-error";
import * as researchService from "@/services/research";

/** One OpenAlex work `paper_suggestion_node` suggested for this run.
 * Mirrors the backend's `paper_suggestion.SuggestedPaper` exactly --
 * see `models.ResearchRun.suggested_papers`. The `import_*` fields are
 * written by Milestone 10 step 3's import chord directly onto this
 * same JSONB entry -- absent until an import has been requested. */
export interface SuggestedPaper {
  openalex_id: string;
  title: string;
  authors: string[];
  year: number | null;
  cited_by_count: number;
  landing_url: string;
  oa_pdf_url: string | null;
  relevance_note: string;
  import_status?: "queued" | "processing" | "added" | "failed";
  imported_asset_id?: string | null;
  import_error?: string | null;
}

/** "These papers could strengthen this answer" (spec section 2), with
 * "Add & re-run" (spec section 3): a paper with an open-access PDF gets
 * a checkbox; a paywalled one keeps only the publisher link. Once at
 * least one is selected, "Add & re-run" imports the selected papers as
 * project assets and -- once every one reaches a final state -- starts
 * one new research run linked back to this one. `onImported` lets the
 * caller (the polling `ResearchRunView`) refetch immediately rather
 * than waiting for the next poll tick. */
export function PaperSuggestionsPanel({
  papers,
  runId,
  onImported,
  rerunRunId,
}: {
  papers: SuggestedPaper[];
  runId: string;
  onImported?: () => void;
  rerunRunId?: string | null;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const mutation = useMutation({
    mutationFn: (openalexIds: string[]) => researchService.importSuggestedPapers(runId, openalexIds),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["research", "run", runId] });
      onImported?.();
    },
  });

  if (papers.length === 0) return null;

  function toggle(openalexId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(openalexId)) next.delete(openalexId);
      else next.add(openalexId);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle>Strengthen this answer</CardTitle>
        {rerunRunId && (
          <Link to={`/research/${rerunRunId}`} className="text-xs font-medium text-primary hover:underline">
            View re-run &rarr;
          </Link>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          These papers could strengthen this answer. Add them to your project.
        </p>
        <ul className="flex flex-col gap-2">
          {papers.map((paper) => (
            <li key={paper.openalex_id} className="flex flex-col gap-1.5 rounded-lg bg-sunken p-3">
              <div className="flex items-start gap-2.5">
                {paper.oa_pdf_url && (
                  <input
                    type="checkbox"
                    aria-label={`Select ${paper.title}`}
                    checked={selected.has(paper.openalex_id)}
                    onChange={() => toggle(paper.openalex_id)}
                    className="mt-0.5 h-4 w-4 shrink-0"
                  />
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{paper.title}</span>
                    {paper.import_status && <StatusBadge domain="paperImport" value={paper.import_status} />}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {paper.authors.length > 0 ? paper.authors.join(", ") : "Unknown authors"}
                    {paper.year ? ` · ${paper.year}` : ""}
                    {" · "}
                    {paper.cited_by_count} citation{paper.cited_by_count === 1 ? "" : "s"}
                  </span>
                  {paper.import_status === "failed" && paper.import_error && (
                    <p className="text-xs text-destructive">{paper.import_error}</p>
                  )}
                  <a
                    href={paper.oa_pdf_url ?? paper.landing_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-fit items-center gap-1 text-xs font-medium text-primary hover:underline"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    {paper.oa_pdf_url ? "Open PDF" : "Open on publisher site"}
                  </a>
                </div>
              </div>
            </li>
          ))}
        </ul>

        {mutation.isError && (
          <p role="alert" className="text-sm text-destructive">
            {messageFor(mutation.error)}
          </p>
        )}

        <div className="flex justify-end">
          <Button
            type="button"
            disabled={selected.size === 0 || mutation.isPending}
            onClick={() => mutation.mutate(Array.from(selected))}
          >
            {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Add &amp; re-run
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/features/research/PaperSuggestionsPanel.test.tsx`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/research/PaperSuggestionsPanel.tsx frontend/src/features/research/PaperSuggestionsPanel.test.tsx
git commit -m "$(cat <<'EOF'
feat(paper-import): add checkboxes, Add & re-run, and per-card status to PaperSuggestionsPanel

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Wire the panel into `ResearchResult`/`ResearchRunView`; keep polling alive during import; show the re-run banner

**Files:**
- Modify: `frontend/src/features/research/ResearchResult.tsx`
- Modify: `frontend/src/features/research/ResearchRunView.tsx`
- Test: `frontend/src/features/research/ResearchRunView.test.tsx` (create if none exists — check first)

**Interfaces:**
- Consumes: `PaperSuggestionsPanel` (Task 9), `ResearchRunDetail.rerun_run_id`/`added_paper_count` (Task 4, via regenerated types from Task 7).
- Produces: `ResearchRunView`'s `isTerminal` predicate also returns `false` while any `suggested_papers` entry is `queued`/`processing`, so `usePolling` keeps polling until every paper (and, if one was added, the re-run's own start) has settled; a "Re-run with N added papers" banner renders when `run.parent_run_id` and `run.added_paper_count` are both present.

- [ ] **Step 1: Check for an existing `ResearchRunView` test file**

Run: `ls frontend/src/features/research/ResearchRunView.test.tsx 2>&1`

If it exists, add the two new tests below to it (matching its existing setup/mocking conventions for `researchService.getResearchRun` — read the file first to match style exactly). If it does not exist, create it fresh per Step 1a below.

- [ ] **Step 1a: Write the failing tests**

Create (or add to) `frontend/src/features/research/ResearchRunView.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResearchRunView } from "@/features/research/ResearchRunView";
import * as researchService from "@/services/research";
import { renderWithProviders } from "@/test/render";
import type { components } from "@/types/api";

type ResearchRunDetail = components["schemas"]["ResearchRunDetail"];

function makeRun(overrides: Partial<ResearchRunDetail> = {}): ResearchRunDetail {
  return {
    id: "run-1",
    project_id: "project-1",
    owner_id: "owner-1",
    task_id: null,
    parent_run_id: null,
    query: "What are the market trends?",
    status: "completed",
    include_assets: true,
    include_web: true,
    max_results: 5,
    objective: null,
    plan: null,
    final_answer: "The answer.",
    citations: [],
    grounding_status: "grounded",
    visualization: null,
    equations: null,
    suggested_papers: null,
    error_message: null,
    celery_task_id: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    created_at: "2026-09-14T00:00:00Z",
    updated_at: "2026-09-14T00:00:00Z",
    steps: [],
    messages: [],
    claims: [],
    rerun_run_id: null,
    added_paper_count: null,
    ...overrides,
  } as ResearchRunDetail;
}

describe("ResearchRunView re-run linkage", () => {
  it("shows a 're-run with N added papers' banner when this run has a parent", async () => {
    vi.spyOn(researchService, "getResearchRun").mockResolvedValue(
      makeRun({ parent_run_id: "run-0", added_paper_count: 2 }),
    );

    renderWithProviders(<ResearchRunView runId="run-1" />);

    expect(await screen.findByText(/re-run with 2 added papers/i)).toBeInTheDocument();
  });

  it("does not show the banner for a run with no parent", async () => {
    vi.spyOn(researchService, "getResearchRun").mockResolvedValue(makeRun());

    renderWithProviders(<ResearchRunView runId="run-1" />);
    await screen.findByText("What are the market trends?");

    expect(screen.queryByText(/re-run with/i)).not.toBeInTheDocument();
  });
});
```

If a `ResearchRunView.test.tsx` already exists with a different mocking convention (e.g. MSW handlers instead of `vi.spyOn`), rewrite these two tests to match that file's existing pattern exactly rather than introducing a second mocking style in the same file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/features/research/ResearchRunView.test.tsx`
Expected: FAIL — the banner text does not exist yet.

- [ ] **Step 3: Wire `PaperSuggestionsPanel`'s new props in `ResearchResult.tsx`**

In `frontend/src/features/research/ResearchResult.tsx`, change both call sites (lines 95 and 260) from:

```tsx
{suggestedPapers.length > 0 && <PaperSuggestionsPanel papers={suggestedPapers} />}
```

to:

```tsx
{suggestedPapers.length > 0 && (
  <PaperSuggestionsPanel papers={suggestedPapers} runId={run.id} rerunRunId={run.rerun_run_id} />
)}
```

- [ ] **Step 4: Keep polling alive while an import is in flight, and add the re-run banner in `ResearchRunView.tsx`**

In `frontend/src/features/research/ResearchRunView.tsx`, change `isTerminal`:

```typescript
const IN_PROGRESS_IMPORT_STATUSES = new Set(["queued", "processing"]);

function isTerminal(run: ResearchRunDetail): boolean {
  if (!TERMINAL_RUN_STATUSES.has(run.status)) return false;
  // Milestone 10 step 3 (Add & re-run): importing a suggested paper
  // happens after this run is already `completed`, so `usePolling`
  // must keep polling through it -- otherwise a card frozen on
  // "Queued" would never update once the run itself stopped changing.
  const hasPendingImport = (run.suggested_papers ?? []).some((paper) =>
    IN_PROGRESS_IMPORT_STATUSES.has((paper as { import_status?: string }).import_status ?? ""),
  );
  return !hasPendingImport;
}
```

Then add the banner, right after the opening `<div className="flex flex-col gap-6">` (before the existing `motion.div` card):

```tsx
{run.parent_run_id && run.added_paper_count != null && (
  <p className="text-sm text-muted-foreground">
    Re-run with {run.added_paper_count} added paper{run.added_paper_count === 1 ? "" : "s"}
  </p>
)}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/features/research/ResearchRunView.test.tsx src/features/research/PaperSuggestionsPanel.test.tsx`
Expected: PASS

- [ ] **Step 6: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS (aside from any pre-existing unrelated failures — do not investigate those, they predate this branch)

- [ ] **Step 7: Manual smoke check in the browser**

Start the frontend dev server (however this project's `.claude/launch.json`/`npm run dev` is configured) pointed at the Dockerized backend on `:8001`, open a completed run that has `suggested_papers` with at least one `oa_pdf_url`, tick a paper, click "Add & re-run", and confirm: the checkbox/button render, the button disables during the request, the card's status updates to "Processing" then "Added"/"Failed" as the worker processes it, and a re-run link appears once one exists.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/research/ResearchResult.tsx frontend/src/features/research/ResearchRunView.tsx frontend/src/features/research/ResearchRunView.test.tsx
git commit -m "$(cat <<'EOF'
feat(paper-import): wire Add & re-run into ResearchResult/ResearchRunView

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Knowledge graph update and full-suite verification

**Files:** none (verification-only task)

- [ ] **Step 1: Update the knowledge graph**

```bash
python -m graphify update .
```

- [ ] **Step 2: Run the full backend test suite**

```bash
docker exec aikdap_backend pytest -q
```

Expected: PASS, aside from the known environmental failures listed in Global Constraints (`test_execution_*`, `test_cross_paper`, `test_fallback_grounding`) — confirm no *other* file regressed.

- [ ] **Step 3: Run the full frontend test suite and typecheck**

```bash
cd frontend && npx vitest run && npm run typecheck
```

(If there's no `typecheck` script, run `npx tsc --noEmit` instead — check `package.json` first, per Task 8 Step 4.)

Expected: PASS.

- [ ] **Step 4: Commit the graph update, if it produced a diff**

```bash
git add graphify-out
git commit -m "$(cat <<'EOF'
chore: update knowledge graph after add-and-rerun

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

If `git status` shows no changes under `graphify-out`, skip this commit.

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** import endpoint (Task 6) · ownership 404 via existing `get_owned_run` (Task 6) · id/oa_pdf_url validation 422 (Task 6) · Celery chord/chord-callback exactly-one-rerun (Task 5) · `parent_run_id` reuse, no new migration (Task 5, documented in Global Constraints) · partial failure still re-runs / all-failed no re-run (Task 5) · in-band "notification" via per-paper status + `rerun_run_id`/`added_paper_count` (Task 4, Task 5) · imported papers are normal assets via existing pipeline (Task 3, Task 5) · SSRF guard: https-only, streamed size cap, capped+re-validated redirects, private/loopback/link-local rejected, PDF-magic content validation (Task 2) · scrubbed error messages (Task 2, Task 5) · frontend checkboxes/button/status/link (Task 9) · Workflow-Timeline-equivalent "Re-run with N added papers" (Task 10 — the only existing per-run display surface in this codebase is `ResearchResult`/`ResearchRunView`, there is no separate run-history/timeline component today) · backend tests per the task's list (Tasks 2, 3, 5, 6) · frontend tests per the task's list (Tasks 9, 10).
- **Known deliberate scope decision:** no notifications module was invented (none exists in this codebase; CLAUDE.md forbids inventing modules). This is called out explicitly to the requester as a ruling, not silently substituted.
