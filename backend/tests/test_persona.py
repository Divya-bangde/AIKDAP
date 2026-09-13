"""Persona storage and the effective-persona rule (spec section 1)."""

import uuid

import pytest

from app.modules.auth.models import Persona, User


@pytest.mark.asyncio
async def test_new_user_defaults_to_researcher(session):
    """Existing and new users keep today's experience unless they choose."""
    user = User(
        email=f"pytest-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    try:
        assert user.persona is Persona.RESEARCHER
    finally:
        await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_project_without_override_inherits_owner_persona(project):
    assert project.persona_override is None
    assert project.effective_persona(Persona.STUDENT) is Persona.STUDENT


@pytest.mark.asyncio
async def test_project_override_wins_over_owner_persona(project, session):
    project.persona_override = Persona.BUILDER
    await session.commit()
    await session.refresh(project)

    assert project.effective_persona(Persona.STUDENT) is Persona.BUILDER
