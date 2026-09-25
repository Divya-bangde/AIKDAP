"""Minimal Kaggle REST client for importing one dataset file.

Talks to the public API directly with httpx (basic auth) instead of the
`kaggle` package, which would add a dependency for two calls."""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from app.core.config.settings import settings

KAGGLE_API = "https://www.kaggle.com/api/v1"
_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")
TABULAR_SUFFIXES = (".csv", ".xlsx", ".xls")


class KaggleError(Exception):
    """Kaggle refused the request or returned something unusable."""


class KaggleNotConfiguredError(KaggleError):
    """No Kaggle credentials in the environment."""


class KaggleFileTooLargeError(KaggleError):
    """The file exceeds the import size limit."""


@dataclass(frozen=True)
class KaggleFile:
    name: str
    size: int


def _check_slug(*values: str) -> None:
    for value in values:
        if not _SLUG.fullmatch(value):
            raise KaggleError(f"Invalid Kaggle identifier '{value}'.")


class KaggleClient:
    def __init__(
        self, username: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float
    ) -> None:
        self._auth = httpx.BasicAuth(username, key)
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=KAGGLE_API, auth=self._auth, transport=self._transport, timeout=self._timeout, follow_redirects=True
        )

    async def list_files(self, owner: str, dataset: str) -> list[KaggleFile]:
        _check_slug(owner, dataset)
        async with self._client() as client:
            response = await client.get(f"/datasets/list/{owner}/{dataset}")
        if response.status_code != 200:
            raise KaggleError(f"Kaggle returned {response.status_code} listing {owner}/{dataset}.")
        return [
            KaggleFile(name=item["name"], size=int(item.get("totalBytes") or 0))
            for item in response.json().get("datasetFiles", [])
            if item["name"].lower().endswith(TABULAR_SUFFIXES)
        ]

    async def download_file(self, owner: str, dataset: str, file_name: str, *, max_bytes: int) -> bytes:
        _check_slug(owner, dataset)
        if not file_name.lower().endswith(TABULAR_SUFFIXES):
            raise KaggleError("Only CSV and XLSX files can be imported.")
        buffer = bytearray()
        is_zip: bool | None = None
        async with self._client() as client:
            async with client.stream(
                "GET", f"/datasets/download/{owner}/{dataset}/{quote(file_name, safe='')}"
            ) as response:
                if response.status_code != 200:
                    raise KaggleError(f"Kaggle returned {response.status_code} downloading {file_name}.")
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if is_zip is None and len(buffer) >= 4:
                        is_zip = bytes(buffer[:4]) == b"PK\x03\x04"
                    # Zip containers carry a small fixed overhead (local/central
                    # directory headers) on top of the member's real size, so the
                    # raw-stream guard needs slack for zips; the true per-member
                    # size is still enforced strictly by `_unzip` below.
                    limit = max_bytes + 4096 if is_zip else max_bytes
                    if len(buffer) > limit:
                        raise KaggleFileTooLargeError(f"{file_name} is larger than {max_bytes // (1024 * 1024)} MB.")
        return _unzip(bytes(buffer), file_name, max_bytes)


def _unzip(content: bytes, file_name: str, max_bytes: int) -> bytes:
    """Kaggle serves some single files zipped; return the member itself."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        wanted = PurePosixPath(file_name).name
        member = next((info for info in archive.infolist() if PurePosixPath(info.filename).name == wanted), None)
        if member is None:
            raise KaggleError(f"{file_name} was not found in Kaggle's archive.")
        if member.file_size > max_bytes:
            raise KaggleFileTooLargeError(f"{file_name} is larger than {max_bytes // (1024 * 1024)} MB.")
        return archive.read(member)


def get_kaggle_client() -> KaggleClient:
    if not settings.kaggle_username or settings.kaggle_key is None:
        raise KaggleNotConfiguredError("Kaggle is not configured.")
    return KaggleClient(settings.kaggle_username, settings.kaggle_key.get_secret_value(), timeout=settings.kaggle_timeout)
