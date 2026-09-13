"""Effective persona on project responses (spec section 1)."""

import uuid

import httpx
import pytest
from sqlalchemy import delete

from app.database.session import async_session_factory
from app.main import app
from app.modules.auth.models import Persona, User
from app.modules.projects.schemas import ProjectRead

PASSWORD = "correct-horse-battery"


@pytest.mark.asyncio
async def test_from_project_resolves_effective_persona(project):
    read = ProjectRead.from_project(project, Persona.STUDENT)

    assert read.persona_override is None
    assert read.effective_persona is Persona.STUDENT
    assert read.id == project.id


@pytest.mark.asyncio
async def test_project_override_is_set_resolved_and_cleared_through_the_api():
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": PASSWORD, "persona": "student"},
            )
            tokens = (
                await client.post(
                    "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
                )
            ).json()
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}

            created = (
                await client.post("/api/v1/projects", json={"name": "P"}, headers=headers)
            ).json()
            assert created["persona_override"] is None
            assert created["effective_persona"] == "student"
            project_url = f"/api/v1/projects/{created['id']}"

            overridden = (
                await client.patch(
                    project_url, json={"persona_override": "builder"}, headers=headers
                )
            ).json()
            assert overridden["persona_override"] == "builder"
            assert overridden["effective_persona"] == "builder"

            listed = (await client.get("/api/v1/projects", headers=headers)).json()
            assert listed[0]["effective_persona"] == "builder"

            cleared = (
                await client.patch(
                    project_url, json={"persona_override": None}, headers=headers
                )
            ).json()
            assert cleared["persona_override"] is None
            assert cleared["effective_persona"] == "student"

            # Changing the user's default changes every non-overridden project.
            await client.patch(
                "/api/v1/auth/me", json={"persona": "researcher"}, headers=headers
            )
            fetched = (await client.get(project_url, headers=headers)).json()
            assert fetched["effective_persona"] == "researcher"
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(User).where(User.email == email))
                await cleanup.commit()
