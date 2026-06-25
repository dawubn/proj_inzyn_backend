from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import UserCreate, UserResponse
from app.services.auth import AuthService

router = APIRouter()


def _auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db))


@router.post("/register", response_model=UserResponse, status_code=201)  # type: ignore[misc]
async def register(data: UserCreate, svc: AuthService = Depends(_auth_service)) -> UserResponse:
    user = await svc.register(data)
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        200: {
            "description": "User logged in. Tokens returned in body AND set as HTTP-only cookies."
        },
        401: {"description": "Invalid credentials"},
    },
)  # type: ignore[misc]
async def login(data: LoginRequest, svc: AuthService = Depends(_auth_service)) -> Response:
    """Login — returns tokens in JSON body and sets secure HTTP-only cookies.

    Use the returned `access_token` as `Authorization: Bearer <token>` in Swagger UI,
    or rely on the cookies that are automatically set for browser clients.
    """
    _user, tokens = await svc.login(data)

    response = JSONResponse(
        content={
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": "bearer",
        }
    )

    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    response.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="strict",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )

    return response


@router.post(
    "/refresh",
    status_code=204,
    responses={
        204: {
            "description": "Tokens refreshed. New tokens set in secure HTTP-only cookies "
            "(access_token with SameSite=Lax, refresh_token with SameSite=Strict)."
        },
        401: {"description": "Invalid or expired refresh token"},
    },
)  # type: ignore[misc]
async def refresh(data: RefreshRequest, svc: AuthService = Depends(_auth_service)) -> Response:
    """Refresh tokens and set secure cookies.

    Takes refresh_token (from cookie or request body) and returns new tokens in secure cookies.
    """
    _user, tokens = await svc.refresh(data.refresh_token)

    response = Response(status_code=204)

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

    return response


@router.post("/logout", status_code=204)  # type: ignore[misc]
async def logout() -> Response:
    """Logout by clearing secure cookies."""
    response = Response(status_code=204)
    response.delete_cookie(key="access_token", secure=settings.APP_ENV == "production")
    response.delete_cookie(key="refresh_token", secure=settings.APP_ENV == "production")
    return response
