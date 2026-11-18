from fastapi import Response

from app.core.config import settings
from app.schemas.auth import TokenResponse


def set_auth_cookies(response: Response, tokens: TokenResponse) -> None:
    """Store the session tokens in secure HTTP-only cookies."""
    secure = settings.APP_ENV == "production"
    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )


def clear_auth_cookies(response: Response) -> None:
    secure = settings.APP_ENV == "production"
    response.delete_cookie(key="access_token", secure=secure)
    response.delete_cookie(key="refresh_token", secure=secure)
