"""Route-level tests for the business analytics API (Milestone 6 Task 7).

Follows `tests/test_reports_routes.py`: drives the real app through
`httpx.ASGITransport` against the real database, replacing only the
Celery enqueue. Every user created here is deleted in a `finally`."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete

from app.database.session import async_session_factory, engine
from app.main import app
from app.modules.assets.enums import AssetProcessingStatus
from app.modules.assets.models import Asset
from app.modules.auth.models import User

PASSWORD = "correct-horse-battery"
SAMPLE_CSV = Path(__file__).resolve().parents[2] / "docs" / "sample-data" / "visualization-testcases.csv"


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_loops():
    """R4: `app.database.session.engine` binds to the first test's event
    loop; each new DB-backed test file disposes it so a later test's
    fresh loop doesn't reuse a pool bound to a closed one."""
    yield
    await engine.dispose()


@pytest.fixture
def enqueued(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.modules.business_analytics.service.run_analysis",
        SimpleNamespace(delay=lambda asset_id: calls.append(asset_id)),
    )
    return calls


@pytest.fixture(autouse=True)
def _no_upload_processing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.assets.service.process_uploaded_asset",
        SimpleNamespace(
            delay=lambda asset_id: SimpleNamespace(id="fake-task-id"),
            name="process_uploaded_asset",
        ),
    )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


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
        await client.post("/api/v1/projects", json={"name": "Analytics route test"}, headers=headers)
    ).json()
    return uuid.UUID(created["id"])


async def _upload_dataset(
    client: httpx.AsyncClient, headers: dict, project_id: uuid.UUID, *, mark_completed: bool = True
) -> uuid.UUID:
    with open(SAMPLE_CSV, "rb") as handle:
        created = (
            await client.post(
                "/api/v1/assets/upload",
                data={"project_id": str(project_id)},
                files={"file": ("visualization-testcases.csv", handle, "text/csv")},
                headers=headers,
            )
        ).json()
    asset_id = uuid.UUID(created["id"])
    if mark_completed:
        await _set_status(asset_id, AssetProcessingStatus.COMPLETED)
    return asset_id


async def _upload_document(client: httpx.AsyncClient, headers: dict, project_id: uuid.UUID) -> uuid.UUID:
    created = (
        await client.post(
            "/api/v1/assets/upload",
            data={"project_id": str(project_id)},
            files={"file": ("notes.txt", b"hello world", "text/plain")},
            headers=headers,
        )
    ).json()
    asset_id = uuid.UUID(created["id"])
    await _set_status(asset_id, AssetProcessingStatus.COMPLETED)
    return asset_id


async def _set_status(
    asset_id: uuid.UUID, status: AssetProcessingStatus, *, plan: dict | None = None
) -> None:
    async with async_session_factory() as session:
        asset = await session.get(Asset, asset_id)
        asset.processing_status = status
        if plan is not None:
            asset.asset_metadata = {
                **asset.asset_metadata,
                "analysis": {**asset.asset_metadata["analysis"], "plan": plan},
            }
        session.add(asset)
        await session.commit()


async def _cleanup(emails: list[str]) -> None:
    async with async_session_factory() as cleanup:
        await cleanup.execute(delete(User).where(User.email.in_(emails)))
        await cleanup.commit()


@pytest.mark.asyncio
async def test_profile_200s_and_caches_on_the_dataset(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id)

            response = await client.get(f"/api/v1/analytics/datasets/{dataset_id}/profile", headers=headers)

            assert response.status_code == 200
            body = response.json()
            assert body["row_count"] > 0
            async with async_session_factory() as session:
                asset = await session.get(Asset, dataset_id)
                assert "profile" in asset.asset_metadata
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_post_analysis_202s_and_enqueues_once(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id)

            response = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "What drives revenue?"},
                headers=headers,
            )

            assert response.status_code == 202
            body = response.json()
            assert body["status"] == "pending"
            assert body["question"] == "What drives revenue?"
            assert len(enqueued) == 1
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_second_analysis_history_contains_first_question(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id)

            first = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "First question?"},
                headers=headers,
            )
            first_id = uuid.UUID(first.json()["id"])
            await _set_status(first_id, AssetProcessingStatus.COMPLETED, plan={"group_by": ["x"]})

            second = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "Second question?"},
                headers=headers,
            )
            second_id = uuid.UUID(second.json()["id"])

            async with async_session_factory() as session:
                asset = await session.get(Asset, second_id)
                history = asset.asset_metadata["analysis"]["history"]
            assert any(item["question"] == "First question?" for item in history)
            assert len(enqueued) == 2
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_get_thread_oldest_first_and_get_one(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id)

            first = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "Q1"},
                headers=headers,
            )
            second = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "Q2"},
                headers=headers,
            )

            thread = await client.get(f"/api/v1/analytics/datasets/{dataset_id}/analyses", headers=headers)
            assert thread.status_code == 200
            questions = [item["question"] for item in thread.json()]
            assert questions == ["Q1", "Q2"]

            one = await client.get(f"/api/v1/analytics/analyses/{first.json()['id']}", headers=headers)
            assert one.status_code == 200
            assert one.json()["question"] == "Q1"
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_another_user_gets_404_on_all_four_routes(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            stranger_headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id)
            analysis = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "Q1"},
                headers=headers,
            )
            analysis_id = analysis.json()["id"]

            profile = await client.get(
                f"/api/v1/analytics/datasets/{dataset_id}/profile", headers=stranger_headers
            )
            post = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "Q2"},
                headers=stranger_headers,
            )
            thread = await client.get(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses", headers=stranger_headers
            )
            one = await client.get(f"/api/v1/analytics/analyses/{analysis_id}", headers=stranger_headers)

            assert profile.status_code == 404
            assert post.status_code == 404
            assert thread.status_code == 404
            assert one.status_code == 404
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_document_asset_409s_on_post_and_profile(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            document_id = await _upload_document(client, headers, project_id)

            profile = await client.get(f"/api/v1/analytics/datasets/{document_id}/profile", headers=headers)
            post = await client.post(
                f"/api/v1/analytics/datasets/{document_id}/analyses",
                json={"question": "Q1"},
                headers=headers,
            )

            assert profile.status_code == 409
            assert post.status_code == 409
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_queued_dataset_409s(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id, mark_completed=False)
            await _set_status(dataset_id, AssetProcessingStatus.QUEUED)

            profile = await client.get(f"/api/v1/analytics/datasets/{dataset_id}/profile", headers=headers)

            assert profile.status_code == 409
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_question_length_validation(enqueued):
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)
            dataset_id = await _upload_dataset(client, headers, project_id)

            empty = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": ""},
                headers=headers,
            )
            too_long = await client.post(
                f"/api/v1/analytics/datasets/{dataset_id}/analyses",
                json={"question": "x" * 1001},
                headers=headers,
            )

            assert empty.status_code == 422
            assert too_long.status_code == 422
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_kaggle_routes_503_when_not_configured(monkeypatch):
    from app.core.config.settings import settings as analytics_settings
    monkeypatch.setattr(analytics_settings, "kaggle_username", None)
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)

            files = await client.get("/api/v1/analytics/kaggle/acme/sales/files", headers=headers)
            imported = await client.post(
                f"/api/v1/projects/{project_id}/analytics/kaggle/import",
                json={"owner": "acme", "dataset": "sales", "file_name": "sales.csv"},
                headers=headers,
            )

            assert files.status_code == 503
            assert imported.status_code == 503
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_kaggle_import_enqueues_when_configured(monkeypatch):
    from app.core.config.settings import settings as analytics_settings
    monkeypatch.setattr(analytics_settings, "kaggle_username", "user")
    monkeypatch.setattr(analytics_settings, "kaggle_key", SecretStr("key"))
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.modules.business_analytics.service.import_kaggle_dataset",
        SimpleNamespace(delay=lambda *args: calls.append(args)),
    )
    emails: list[str] = []
    async with _client() as client:
        try:
            headers, _ = await _register(client, emails)
            project_id = await _create_project(client, headers)

            imported = await client.post(
                f"/api/v1/projects/{project_id}/analytics/kaggle/import",
                json={"owner": "acme", "dataset": "sales", "file_name": "sales.csv"},
                headers=headers,
            )

            assert imported.status_code == 202
            assert len(calls) == 1
        finally:
            await _cleanup(emails)


@pytest.mark.asyncio
async def test_kaggle_import_other_users_project_is_404(monkeypatch):
    from app.core.config.settings import settings as analytics_settings
    monkeypatch.setattr(analytics_settings, "kaggle_username", "user")
    monkeypatch.setattr(analytics_settings, "kaggle_key", SecretStr("key"))
    emails: list[str] = []
    async with _client() as client:
        try:
            headers_a, _ = await _register(client, emails)
            project_id = await _create_project(client, headers_a)
            headers_b, _ = await _register(client, emails)

            imported = await client.post(
                f"/api/v1/projects/{project_id}/analytics/kaggle/import",
                json={"owner": "acme", "dataset": "sales", "file_name": "sales.csv"},
                headers=headers_b,
            )

            assert imported.status_code == 404
        finally:
            await _cleanup(emails)
