"""Pydantic v2 request/response schemas for the auth module."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.auth.models import Persona


class UserCreate(BaseModel):
    """Payload for registering a new user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    persona: Persona


class UserRead(BaseModel):
    """Public representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    created_at: datetime
    persona: Persona


class UserUpdate(BaseModel):
    """Payload for updating the current user's own settings."""

    persona: Persona


class LoginRequest(BaseModel):
    """Payload for authenticating with email and password."""

    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    """Payload for exchanging a refresh token for a new token pair."""

    refresh_token: str


class TokenPair(BaseModel):
    """An access/refresh JWT pair returned on login or token refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
