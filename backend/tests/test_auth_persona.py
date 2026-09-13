"""Persona at signup and on the current user (spec section 1).

Drives the real app through `httpx.ASGITransport` against the real
database, so routing, dependency wiring, and persistence are all
exercised. Lifespan is not run, so no external service is contacted.
"""

import uuid

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete

from app.database.session import async_session_factory
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.schemas import UserCreate

PASSWORD = "correct-horse-battery"


def test_signup_requires_a_persona():
    with pytest.raises(ValidationError):
        UserCreate(email="someone@example.com", password=PASSWORD)


@pytest.mark.asyncio
async def test_signup_stores_persona_and_the_user_can_change_it():
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            registered = await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": PASSWORD, "persona": "student"},
            )
            assert registered.status_code == 201
            assert registered.json()["persona"] == "student"

            tokens = (
                await client.post(
                    "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
                )
            ).json()
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}

            updated = await client.patch(
                "/api/v1/auth/me", json={"persona": "builder"}, headers=headers
            )
            assert updated.status_code == 200
            assert updated.json()["persona"] == "builder"

            me = await client.get("/api/v1/auth/me", headers=headers)
            assert me.json()["persona"] == "builder"

            rejected = await client.patch(
                "/api/v1/auth/me", json={"persona": "wizard"}, headers=headers
            )
            assert rejected.status_code == 422
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(User).where(User.email == email))
                await cleanup.commit()
