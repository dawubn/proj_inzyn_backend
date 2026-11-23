from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import structlog

from app.core.config import settings
from app.core.exceptions import InvalidPasswordError, InvalidResetTokenError, ValidationError
from app.core.security import (
    generate_password_reset_token,
    hash_password,
    hash_password_reset_token,
    verify_password,
)
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.repositories.password_reset_token import PasswordResetTokenRepository
from app.repositories.user import UserRepository

logger = structlog.get_logger(__name__)


def build_reset_url(token: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?{urlencode({'token': token})}"


class PasswordResetService:
    def __init__(self, user_repo: UserRepository, token_repo: PasswordResetTokenRepository) -> None:
        self._users = user_repo
        self._tokens = token_repo

    async def request_reset(self, email: str) -> tuple[User, str] | None:
        """Create a reset token for the account with this email.

        Returns the user and the token to send, or None when nothing should be sent
        (unknown or deactivated account, a link was requested a moment ago, or the
        account already got PASSWORD_RESET_MAX_PER_DAY links within 24 hours).
        """
        user = await self._users.get_by_email(email)
        if not user or not user.is_active:
            return None

        now = datetime.now(UTC)
        latest = await self._tokens.get_latest_for_user(user.id)
        cooldown = timedelta(seconds=settings.PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS)
        if latest and now - latest.created_at < cooldown:
            return None

        sent_today = await self._tokens.count_created_since(user.id, now - timedelta(days=1))
        if sent_today >= settings.PASSWORD_RESET_MAX_PER_DAY:
            logger.warning("Daily limit of password reset emails reached", user_id=str(user.id))
            return None

        await self._tokens.invalidate_for_user(user.id, now)

        token = generate_password_reset_token()
        await self._tokens.create(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_password_reset_token(token),
                expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
            )
        )
        logger.info("Password reset requested", user_id=str(user.id))
        return user, token

    async def reset_password(self, token: str, new_password: str) -> User:
        now = datetime.now(UTC)
        reset_token = await self._tokens.get_by_hash(hash_password_reset_token(token))
        if not reset_token or reset_token.used_at is not None or reset_token.expires_at <= now:
            raise InvalidResetTokenError

        user = await self._users.get_by_id(reset_token.user_id)
        if not user or not user.is_active:
            raise InvalidResetTokenError

        # The link came to the account's email, which confirms the address as well
        user.is_verified = True
        await self._set_password(user, new_password, now)
        logger.info("Password reset completed", user_id=str(user.id))
        return user

    async def change_password(self, user: User, current_password: str, new_password: str) -> User:
        """Change the password of a logged-in user, who has to confirm the current one."""
        if not verify_password(current_password, user.hashed_password):
            raise InvalidPasswordError
        if current_password == new_password:
            raise ValidationError("New password must be different from the current one")

        await self._set_password(user, new_password, datetime.now(UTC))
        logger.info("Password changed", user_id=str(user.id))
        return user

    async def _set_password(self, user: User, new_password: str, now: datetime) -> None:
        """Save the new password; sessions and reset links created before it stop working."""
        user.hashed_password = hash_password(new_password)
        user.password_changed_at = now
        await self._users.update(user)
        await self._tokens.invalidate_for_user(user.id, now)
