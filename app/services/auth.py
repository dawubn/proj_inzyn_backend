import structlog

from app.core.config import settings
from app.core.exceptions import ConflictError, EmailNotVerifiedError, UnauthorizedError
from app.core.security import (
    PASSWORD_VERSION_CLAIM,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_token_for_current_password,
    password_version,
    verify_password,
)
from app.enums.analysis import UserRole
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserCreate

logger = structlog.get_logger(__name__)


class AuthService:
    def __init__(self, user_repo: UserRepository) -> None:
        self._users = user_repo

    async def register(self, data: UserCreate) -> User:
        if await self._users.email_exists(data.email):
            raise ConflictError(f"User with email {data.email} already exists")

        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            name=data.name,
            surname=data.surname,
            company=data.company,
            role=UserRole.USER,
            # Without required verification the account can be used right away
            is_verified=not settings.EMAIL_VERIFICATION_REQUIRED,
        )
        return await self._users.create(user)

    async def login(self, data: LoginRequest) -> tuple[User, TokenResponse]:
        user = await self._users.get_by_email(data.email)
        if not user or not verify_password(data.password, user.hashed_password):
            raise UnauthorizedError("Invalid credentials")

        if not user.is_active:
            raise UnauthorizedError("Account is deactivated")

        # Checked after the password, so it does not tell anyone which emails are registered
        if settings.EMAIL_VERIFICATION_REQUIRED and not user.is_verified:
            raise EmailNotVerifiedError

        logger.info("User logged in", user_id=str(user.id))
        return user, self.issue_tokens(user)

    async def refresh(self, refresh_token: str) -> tuple[User, TokenResponse]:
        try:
            payload = decode_token(refresh_token)
        except ValueError as exc:
            raise UnauthorizedError("Invalid refresh token") from exc

        if payload.get("type") != "refresh":
            raise UnauthorizedError("Not a refresh token")

        user = await self._users.get_by_id(payload["sub"])
        if not user or not user.is_active:
            raise UnauthorizedError("User not found or deactivated")

        if not is_token_for_current_password(payload, user.password_changed_at):
            raise UnauthorizedError("Session expired")

        return user, self.issue_tokens(user)

    @staticmethod
    def issue_tokens(user: User) -> TokenResponse:
        version = {PASSWORD_VERSION_CLAIM: password_version(user.password_changed_at)}
        return TokenResponse(
            access_token=create_access_token(str(user.id), {"role": user.role, **version}),
            refresh_token=create_refresh_token(str(user.id), version),
        )
