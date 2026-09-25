"""Tests for the Kaggle REST client (Milestone 6 Task 8)."""

import io
import zipfile

import httpx
import pytest

from app.integrations.kaggle.client import KaggleClient, KaggleError, KaggleFileTooLargeError

pytestmark = pytest.mark.asyncio


def client(handler) -> KaggleClient:
    return KaggleClient("user", "key", transport=httpx.MockTransport(handler), timeout=5)


async def test_lists_only_tabular_files():
    def handler(request):
        assert request.headers["authorization"].startswith("Basic ")
        assert request.url.path == "/api/v1/datasets/list/acme/sales"
        return httpx.Response(
            200,
            json={
                "datasetFiles": [
                    {"name": "sales.csv", "totalBytes": 10},
                    {"name": "readme.md", "totalBytes": 3},
                    {"name": "q.xlsx", "totalBytes": 7},
                ]
            },
        )

    files = await client(handler).list_files("acme", "sales")
    assert [(f.name, f.size) for f in files] == [("sales.csv", 10), ("q.xlsx", 7)]


async def test_download_plain_file():
    content = b"a,b\n1,2\n"
    data = await client(lambda r: httpx.Response(200, content=content)).download_file(
        "acme", "sales", "sales.csv", max_bytes=100
    )
    assert data == content


async def test_download_unzips():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sales.csv", "a\n1\n")
    data = await client(lambda r: httpx.Response(200, content=buffer.getvalue())).download_file(
        "acme", "sales", "sales.csv", max_bytes=100
    )
    assert data == b"a\n1\n"


async def test_download_over_limit_aborts():
    with pytest.raises(KaggleFileTooLargeError):
        await client(lambda r: httpx.Response(200, content=b"x" * 101)).download_file(
            "acme", "sales", "s.csv", max_bytes=100
        )


async def test_http_error_raises_kaggle_error():
    with pytest.raises(KaggleError):
        await client(lambda r: httpx.Response(404)).list_files("acme", "missing")


async def test_rejects_bad_slug():
    with pytest.raises(KaggleError):
        await client(lambda r: httpx.Response(200)).list_files("../etc", "x")
