"""Route-level tests for the build-plan endpoint (Milestone 10 step 5).

Drives the real app through `httpx.ASGITransport` against the real
database, following `tests/test_project_persona.py` and
`tests/test_reports_routes.py`. The Celery enqueue is replaced; no LLM
is ever called. Every user created here is deleted in a `finally`
(cascading to projects and assets).
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete

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


async def _register(client: httpx.AsyncClient, emails: list[str]) -> dict:
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    emails.append(email)
    await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "persona": "builder"}
    )
    tokens = (
        await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_project(client: httpx.AsyncClient, headers: dict) -> uuid.UUID:
    created = (
        await client.post(
            "/api/v1/projects", json={"name": "Build plan route test"}, headers=headers
        )
    ).json()
    return uuid.UUID(created["id"])


async def _document(
    client: httpx.AsyncClient,
    headers: dict,
    project_id: uuid.UUID,
    *,
    processed: bool = True,
) -> uuid.UUID:
    """Insert one DOCUMENT asset directly -- the upload pipeline is not
    what these tests exercise, and a real upload would run extraction."""
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    owner_id = uuid.UUID(me["id"])
    async with async_session_factory() as session:
        asset = Asset(
            project_id=project_id,
            owner_id=owner_id,
            title="A Paper",
            description=None,
            asset_type=AssetType.DOCUMENT,
            status=AssetStatus.ACTIVE,
            mime_type="application/pdf",
            file_name="paper.pdf",
            file_extension="pdf",
            file_size=1024,
            storage_path="/tmp/paper.pdf",
            checksum="abc",
            source=AssetSource.UPLOAD,
            version=1,
            tags=[],
            asset_metadata={},
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=(
                AssetProcessingStatus.COMPLETED if processed else AssetProcessingStatus.PENDING
            ),
        )
        session.add(asset)
        await session.commit()
        return asset.id


async def _cleanup(emails: list[str]) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email.in_(emails)))
        await session.commit()


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_build_plan_accepts_a_selection_and_enqueues_one_report(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        asset_id = await _document(client, headers, project_id)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(asset_id)]},
            headers=headers,
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert enqueued == [body["asset_id"]]

        # The report is a GENERATED REPORT asset carrying the build-plan kind.
        report = (await client.get(f"/api/v1/reports/{body['asset_id']}", headers=headers)).json()
        assert report["asset_type"] == "report"
        assert report["source"] == "generated"
        assert report["metadata"]["kind"] == "build_plan"
        assert report["metadata"]["asset_ids"] == [str(asset_id)]
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_404s_for_another_users_project(client, enqueued):
    emails: list[str] = []
    try:
        owner_headers = await _register(client, emails)
        project_id = await _create_project(client, owner_headers)
        asset_id = await _document(client, owner_headers, project_id)

        intruder_headers = await _register(client, emails)
        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(asset_id)]},
            headers=intruder_headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found."
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_404s_for_an_asset_from_another_project(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        await _document(client, headers, project_id)

        other_project_id = await _create_project(client, headers)
        foreign_asset_id = await _document(client, headers, other_project_id)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(foreign_asset_id)]},
            headers=headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found."
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_422s_when_the_project_has_no_processed_documents(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        unprocessed_id = await _document(client, headers, project_id, processed=False)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(unprocessed_id)]},
            headers=headers,
        )

        assert response.status_code == 422
        assert "no processed documents" in response.json()["detail"]
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_422s_for_an_empty_selection(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        await _document(client, headers, project_id)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": []},
            headers=headers,
        )

        assert response.status_code == 422
        assert enqueued == []
    finally:
        await _cleanup(emails)


@pytest.mark.asyncio
async def test_build_plan_422s_when_the_selection_names_only_unprocessed_documents(client, enqueued):
    emails: list[str] = []
    try:
        headers = await _register(client, emails)
        project_id = await _create_project(client, headers)
        # The project does have a processed document, so this is not the
        # "no processed documents" case -- the selection itself is unusable.
        await _document(client, headers, project_id)
        unprocessed_id = await _document(client, headers, project_id, processed=False)

        response = await client.post(
            f"/api/v1/projects/{project_id}/reports/build-plan",
            json={"asset_ids": [str(unprocessed_id)]},
            headers=headers,
        )

        assert response.status_code == 422
        assert "still processing" in response.json()["detail"]
        assert enqueued == []
    finally:
        await _cleanup(emails)
