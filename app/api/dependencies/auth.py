from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import AsyncSession, get_db
from app.enums.analysis import UserRole
from app.models.user import User
from app.repositories.user import UserRepository

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("Not an access token")

    # --- MOCK: szuka usera w pliku JSON zamiast bazy ---
    if settings.APP_ENV == "development" and settings.APP_DEBUG:
        from app.core.mock_users import MOCK_USERS

        user = next((u for u, _ in MOCK_USERS.values() if str(u.id) == payload.get("sub")), None)
        if user is not None:
            if not user.is_active:
                raise UnauthorizedError("User not found or deactivated")
            return user
    # --- koniec MOCK ---

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(payload["sub"])
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or deactivated")

    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return current_user
