"""Password hashing and JWT issuance/validation.

Also hosts `get_current_user`, the FastAPI dependency that resolves the
authenticated user from a bearer access token, since that resolution is
inseparable from JWT validation.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"
#: A short-lived token that authorizes reading ONE step stream. Exists
#: because the browser's `EventSource` cannot send an Authorization
#: header, and a normal access token must never appear in a URL.
STREAM_TOKEN_TYPE = "stream"
STREAM_TOKEN_TTL_SECONDS = 60

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_scheme = HTTPBearer(auto_error=True)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against its bcrypt hash."""
    return _pwd_context.verify(plain_password, hashed_password)


def _create_token(subject: uuid.UUID, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: uuid.UUID) -> str:
    """Issue a short-lived access token for the given user id."""
    return _create_token(
        subject, ACCESS_TOKEN_TYPE, timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: uuid.UUID) -> str:
    """Issue a long-lived refresh token for the given user id."""
    return _create_token(
        subject, REFRESH_TOKEN_TYPE, timedelta(days=settings.refresh_token_expire_days)
    )


def create_stream_token(subject: uuid.UUID, resource: str) -> str:
    """A ~60s token scoped to one stream `resource` (e.g. "run:<id>")."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "type": STREAM_TOKEN_TYPE,
        "res": resource,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=STREAM_TOKEN_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_stream_token(token: str, *, resource: str) -> uuid.UUID:
    """The user a stream token was issued to; 401 unless it is a valid,
    unexpired stream token for exactly `resource`."""
    user_id = decode_token(token, expected_type=STREAM_TOKEN_TYPE)
    # Re-decoding is cheap and keeps `decode_token`'s contract unchanged.
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    if payload.get("res") != resource:
        raise _CREDENTIALS_ERROR
    return user_id


def decode_token(token: str, *, expected_type: str) -> uuid.UUID:
    """Decode and validate a JWT, returning the subject user id.

    Raises `HTTPException(401)` if the token is malformed, expired, or
    not of the expected type (access vs. refresh).
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except JWTError as exc:
        raise _CREDENTIALS_ERROR from exc

    if payload.get("type") != expected_type:
        raise _CREDENTIALS_ERROR

    subject = payload.get("sub")
    if subject is None:
        raise _CREDENTIALS_ERROR

    try:
        return uuid.UUID(subject)
    except ValueError as exc:
        raise _CREDENTIALS_ERROR from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the currently authenticated user from a bearer access token."""
    user_id = decode_token(credentials.credentials, expected_type=ACCESS_TOKEN_TYPE)
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user
