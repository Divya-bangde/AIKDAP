"""Route-level tests for the reports API (hardening item 3).

Drives the real app through `httpx.ASGITransport` against the real
database, following `tests/test_project_persona.py`. The Celery enqueue
is replaced; no LLM is ever called. Every user created here is deleted
in a `finally` (cascading to projects and assets)."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.database.session import async_session_factory
from app.main import app
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.auth.models import User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def enqueued(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.modules.reports.service.generate_report",
        SimpleNamespace(delay=lambda asset_id: calls.append(asset_id)),
    )
    return calls


async def _register(client: httpx.AsyncClient, emails: list[str]) -> tuple[dict, uuid.UUID]:
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    emails.append(email)
    await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "persona": "student"}
    )
    tokens = (
        await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return headers, uuid.UUID(me["id"])


async def _create_project(client: httpx.AsyncClient, headers: dict) -> uuid.UUID:
    created = (
        await client.post("/api/v1/projects", json={"name": "Reports route test"}, headers=headers)
    ).json()
    return uuid.UUID(created["id"])


async def _report(owner_id: uuid.UUID, project_id: uuid.UUID, status: AssetProcessingStatus) -> uuid.UUID:
    async with async_session_factory() as session:
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title="Study Summary",
            description=None,
            asset_type=AssetType.SUMMARY,
            status=AssetStatus.ACTIVE,
            mime_type="application/json",
            file_name="study-summary.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[],
            asset_metadata={"kind": "study_summary", "sections": []},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=status,
            processing_error=(
                "Report generation failed (RuntimeError)."
                if status is AssetProcessingStatus.FAILED
                else None
            ),
        )
        session.add(asset)
        await session.commit()
        return asset.id


async def _cleanup(emails: list[str]) -> None:
    async with async_session_factory() as cleanup:
        await cleanup.execute(delete(User).where(User.email.in_(emails)))
        await cleanup.commit()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_synopsis_404s_for_another_users_project_and_422s_without_documents(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            owner_headers, _ = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, owner_headers)
            url = f"/api/v1/projects/{project_id}/reports/synopsis"

            stranger = await client.post(url, json={"kind": "study_summary"}, headers=stranger_headers)
            empty = await client.post(url, json={"kind": "study_summary"}, headers=owner_headers)

            assert stranger.status_code == 404
            assert empty.status_code == 422
            assert enqueued == []
            async with async_session_factory() as session:
                count = await session.scalar(
                    select(func.count()).select_from(Asset).where(Asset.project_id == project_id)
                )
            assert count == 0
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_download_404s_for_a_non_owner_and_409s_until_complete():
    emails: list[str] = []
    async with _client() as client:
        try:
            owner_headers, owner_id = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, owner_headers)
            report_id = await _report(owner_id, project_id, AssetProcessingStatus.RUNNING)
            url = f"/api/v1/reports/{report_id}/download?format=docx"

            stranger = await client.get(url, headers=stranger_headers)
            not_ready = await client.get(url, headers=owner_headers)

            assert stranger.status_code == 404
            assert not_ready.status_code == 409
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_retry_404s_for_a_non_owner_409s_unless_failed_and_202s_a_failed_report(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            owner_headers, owner_id = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, owner_headers)
            completed_id = await _report(owner_id, project_id, AssetProcessingStatus.COMPLETED)
            failed_id = await _report(owner_id, project_id, AssetProcessingStatus.FAILED)

            stranger = await client.post(f"/api/v1/reports/{failed_id}/retry", headers=stranger_headers)
            not_failed = await client.post(f"/api/v1/reports/{completed_id}/retry", headers=owner_headers)
            accepted = await client.post(f"/api/v1/reports/{failed_id}/retry", headers=owner_headers)

            assert stranger.status_code == 404
            assert not_failed.status_code == 409
            assert accepted.status_code == 202
            assert accepted.json() == {"asset_id": str(failed_id), "status": "pending"}
            assert enqueued == [str(failed_id)]

            report = await client.get(f"/api/v1/reports/{failed_id}", headers=owner_headers)
            assert report.status_code == 200
            assert report.json()["processing_status"] == "pending"
            assert report.json()["processing_error"] is None
            assert report.json()["steps"] == []
            assert (
                await client.get(f"/api/v1/reports/{failed_id}", headers=stranger_headers)
            ).status_code == 404
        finally:
            await _cleanup(emails)
