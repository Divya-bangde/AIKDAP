"""Data-access layer for the `User` model.

Contains only persistence operations; transaction boundaries (commit)
and business rules live in `service.py`.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import Persona, User


class UserRepository:
    """Encapsulates all direct database access for `User` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch a user by primary key, or None if not found."""
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Fetch a user by email, or None if not found."""
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        hashed_password: str,
        full_name: str | None,
        persona: Persona,
    ) -> User:
        """Insert a new user row and flush to populate generated fields."""
        user = User(
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            persona=persona,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user
