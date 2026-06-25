from fastapi import Cookie, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db
from app.enums.analysis import UserRole
from app.models.user import User
from app.repositories.user import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    access_token: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current user from Authorization header or access_token cookie."""
    token = None

    # Priority 1: Authorization Bearer token
    if credentials:
        token = credentials.credentials
    # Priority 2: access_token cookie
    elif access_token:
        token = access_token
    else:
        raise UnauthorizedError("Missing access token")

    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("Not an access token")

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(payload["sub"])
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or deactivated")

    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return current_user
