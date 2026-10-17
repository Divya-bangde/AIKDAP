"""API regression over real PostgreSQL: >100 records, ties and owner boundaries."""

import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.assets.enums import AssetProcessingStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.projects.models import Project
from app.modules.research.enums import ResearchGroundingStatus, ResearchRunStatus
from app.modules.research.models import ResearchRun


@pytest.mark.asyncio
async def test_totals_and_complete_histories_with_tied_timestamps(session, project):
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    projects = [project] + [Project(owner_id=project.owner_id, name=f"Project {index}",
                                   created_at=timestamp) for index in range(106)]
    project.created_at = timestamp
    session.add_all(projects)
    assets = [Asset(project_id=project.id, owner_id=project.owner_id,
                    title=f"Document {index}", asset_type=AssetType.DOCUMENT,
                    mime_type="text/plain", file_name="document.txt", file_extension=".txt",
                    file_size=20, storage_path="test/unused", checksum=str(index),
                    created_at=timestamp, processing_status=AssetProcessingStatus.COMPLETED,
                    asset_metadata={"large_understanding": "x" * 10000, "kind": "document"},
                    ai_profile={"status": "completed", "embedding_status": "completed", "summary": "x" * 10000})
              for index in range(107)]
    runs = [ResearchRun(project_id=project.id, owner_id=project.owner_id,
                        query=f"Question {index}", status=ResearchRunStatus.COMPLETED,
                        grounding_status=ResearchGroundingStatus.GROUNDED if index < 103
                        else ResearchGroundingStatus.INSUFFICIENT_EVIDENCE,
                        final_answer="A supported answer" if index < 103 else None,
                        created_at=timestamp) for index in range(107)]
    session.add_all(assets + runs)
    stranger = User(email=f"phase1-{uuid.uuid4()}@example.test", hashed_password="unused", full_name="Other")
    session.add(stranger)
    await session.flush()
    other_project = Project(owner_id=stranger.id, name="Foreign")
    session.add(other_project)
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(project.owner_id)}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(stranger.id)}"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            summary = await client.get("/api/v1/projects/summary", headers=headers)
            assert summary.status_code == 200, summary.text
            assert summary.json() == dict(projects=107, assets=107, documents=107, runs=107, grounded_answers=103)
            scoped = await client.get("/api/v1/projects/summary", params={"project_id": str(project.id)}, headers=headers)
            assert scoped.json()["projects"] == 1 and scoped.json()["assets"] == 107
            grounded_ids, grounded_cursor = [], None
            while True:
                response = await client.get("/api/v1/research/runs/page", headers=headers,
                    params={"grounding_status": "grounded", "limit": 30,
                            **({"cursor": grounded_cursor} if grounded_cursor else {})})
                assert response.status_code == 200, response.text
                body = response.json()
                grounded_ids.extend(item["id"] for item in body["items"])
                assert all(item["grounding_status"] == "grounded" for item in body["items"])
                grounded_cursor = body["next_cursor"]
                if not grounded_cursor:
                    break
            assert len(grounded_ids) == len(set(grounded_ids)) == summary.json()["grounded_answers"]
            denied = await client.get("/api/v1/projects/summary", params={"project_id": str(project.id)}, headers=other_headers)
            assert denied.status_code == 404
            for path, rows in (("projects/page", projects), ("assets/page", assets), ("research/runs/page", runs)):
                identifiers, cursor, first_cursor = [], None, None
                while True:
                    response = await client.get(f"/api/v1/{path}", headers=headers,
                                                params={"limit": 31, **({"cursor": cursor} if cursor else {})})
                    assert response.status_code == 200, response.text
                    body = response.json()
                    identifiers.extend(item["id"] for item in body["items"])
                    if path == "assets/page":
                        assert all("large_understanding" not in item["metadata"] for item in body["items"])
                        assert all(item["ai_profile"]["status"] == "completed" for item in body["items"])
                        assert all(item["ai_profile"]["summary"] is None for item in body["items"])
                    if path == "research/runs/page":
                        assert all("final_answer" not in item and "citations" not in item for item in body["items"])
                    cursor = body["next_cursor"]
                    first_cursor = first_cursor or cursor
                    if not cursor:
                        break
                assert identifiers == [str(row.id) for row in sorted(rows, key=lambda row: row.id, reverse=True)]
                assert len(identifiers) == len(set(identifiers)) == 107
                foreign_cursor = await client.get(f"/api/v1/{path}", headers=other_headers,
                                                  params={"cursor": first_cursor})
                assert foreign_cursor.status_code == 422
                bad_cursor = await client.get(f"/api/v1/{path}", headers=headers, params={"cursor": "not-a-cursor"})
                assert bad_cursor.status_code == 422
            for path in ("assets/page", "research/runs/page", "knowledge-base/readiness", "knowledge-base/rebuild"):
                method = client.post if path.endswith("rebuild") else client.get
                denied = await method(f"/api/v1/{path}", params={"project_id": str(project.id)}, headers=other_headers)
                assert denied.status_code == 404
            other = await client.get("/api/v1/projects/summary", headers=other_headers)
            assert other.json() == dict(projects=1, assets=0, documents=0, runs=0, grounded_answers=0)
    finally:
        await session.delete(stranger)
        await session.commit()
