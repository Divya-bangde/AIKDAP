"""Business logic for user registration, authentication, and token issuance."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.auth.models import Persona, User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import TokenPair, UserCreate
from app.modules.auth.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class EmailAlreadyRegisteredError(Exception):
    """Raised when registering an email that already has an account."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials do not match any active user."""


class AuthService:
    """Coordinates registration, authentication, and JWT issuance."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = UserRepository(session)

    async def register(self, data: UserCreate) -> User:
        """Create a new user account, rejecting already-registered emails."""
        email = data.email.strip().lower()
        if await self._repository.get_by_email(email) is not None:
            raise EmailAlreadyRegisteredError(email)

        user = await self._repository.create(
            email=email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
            persona=data.persona,
        )
        await self._session.commit()
        return user

    async def update_persona(self, user: User, persona: Persona) -> User:
        """Change the user's default persona."""
        user.persona = persona
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def authenticate(self, email: str, password: str) -> User:
        """Verify credentials and return the matching active user."""
        user = await self._repository.get_by_email(email.strip().lower())
        if (
            user is None
            or not user.is_active
            or not verify_password(password, user.hashed_password)
        ):
            raise InvalidCredentialsError
        return user

    def issue_tokens(self, user: User) -> TokenPair:
        """Issue a new access/refresh token pair for a user."""
        return TokenPair(
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Validate a refresh token and issue a new token pair."""
        user_id = decode_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE)
        user = await self._repository.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError
        return self.issue_tokens(user)


async def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    """FastAPI dependency provider for `AuthService`."""
    return AuthService(session)
