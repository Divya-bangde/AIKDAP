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
