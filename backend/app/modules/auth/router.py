"""HTTP routes for registration, login, token refresh, and current user."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    RefreshTokenRequest,
    TokenPair,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.modules.auth.security import get_current_user
from app.modules.auth.service import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    get_auth_service,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    service: AuthService = Depends(get_auth_service),
) -> User:
    """Register a new user account."""
    try:
        return await service.register(data)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        ) from exc


@router.post("/login", response_model=TokenPair)
async def login(
    data: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenPair:
    """Authenticate with email and password and receive a token pair."""
    try:
        user = await service.authenticate(data.email, data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return service.issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    data: RefreshTokenRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenPair:
    """Exchange a valid refresh token for a new access/refresh token pair."""
    try:
        return await service.refresh(data.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    """Return the currently authenticated user."""
    return current_user


@router.patch("/me", response_model=UserRead)
async def update_current_user(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
) -> User:
    """Update the current user's own settings."""
    return await service.update_persona(current_user, data.persona)
