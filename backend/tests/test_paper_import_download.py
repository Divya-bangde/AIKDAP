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
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": "https://raw.githubusercontent.com/paper.pdf"})
        return httpx.Response(200, content=_PDF_BYTES)

    content = await download_oa_pdf(
        "https://github.com/paper.pdf",
        timeout=5.0,
        max_bytes=1_000_000,
        max_redirects=3,
        transport=_transport(handler),
    )
    assert content == _PDF_BYTES


async def test_rejects_redirect_to_a_private_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/evil.pdf"})
        return httpx.Response(200, content=_PDF_BYTES)

    with pytest.raises(PaperDownloadError, match="private or internal"):
        await download_oa_pdf(
            "https://github.com/paper.pdf",
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
