from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, RefreshRequest
from app.schemas.user import UserCreate, UserResponse
from app.services.auth import AuthService

router = APIRouter()


def _auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db))


@router.post("/register", response_model=UserResponse, status_code=201)  # type: ignore[misc]
async def register(data: UserCreate, svc: AuthService = Depends(_auth_service)) -> UserResponse:
    user = await svc.register(data)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse)  # type: ignore[misc]
async def login(data: LoginRequest, svc: AuthService = Depends(_auth_service)) -> Response:
    """Login and set secure cookies for tokens."""
    import json

    user, tokens = await svc.login(data)

    response = Response(status_code=200)

    # Set access token cookie (HttpOnly, Secure, SameSite=Lax)
    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    # Set refresh token cookie (HttpOnly, Secure, SameSite=Strict)
    response.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="strict",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )

    # Return user info in response body
    response.headers["content-type"] = "application/json"
    response.body = json.dumps(UserResponse.model_validate(user).model_dump()).encode()
    return response


@router.post("/refresh", response_model=UserResponse)  # type: ignore[misc]
async def refresh(data: RefreshRequest, svc: AuthService = Depends(_auth_service)) -> Response:
    """Refresh tokens and set secure cookies."""
    import json

    user, tokens = await svc.refresh(data.refresh_token)

    response = Response(status_code=200)

    # Set access token cookie
    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    # Set refresh token cookie
    response.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="strict",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )

    # Return user info
    response.headers["content-type"] = "application/json"
    response.body = json.dumps(UserResponse.model_validate(user).model_dump()).encode()
    return response


@router.post("/logout", status_code=204)  # type: ignore[misc]
async def logout() -> Response:
    """Logout by clearing secure cookies."""
    response = Response(status_code=204)
    response.delete_cookie(key="access_token", secure=settings.APP_ENV == "production")
    response.delete_cookie(key="refresh_token", secure=settings.APP_ENV == "production")
    return response
