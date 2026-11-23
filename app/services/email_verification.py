from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import structlog

from app.core.config import settings
from app.core.exceptions import InvalidVerificationTokenError
from app.core.security import generate_password_reset_token, hash_password_reset_token
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User
from app.repositories.email_verification_token import EmailVerificationTokenRepository
from app.repositories.user import UserRepository

logger = structlog.get_logger(__name__)


def build_verification_url(token: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?{urlencode({'token': token})}"


class EmailVerificationService:
    """Confirms that the owner of a new account has access to its email address.

    Only used when EMAIL_VERIFICATION_REQUIRED is on (production).
    """

    def __init__(
        self, user_repo: UserRepository, token_repo: EmailVerificationTokenRepository
    ) -> None:
        self._users = user_repo
        self._tokens = token_repo

    async def create_link_token(self, user: User) -> str:
        """Create a new verification token; earlier links of the user stop working."""
        now = datetime.now(UTC)
        await self._tokens.invalidate_for_user(user.id, now)

        token = generate_password_reset_token()
        await self._tokens.create(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=hash_password_reset_token(token),
                expires_at=now + timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS),
            )
        )
        return token

    async def request_new_link(self, email: str) -> tuple[User, str] | None:
        """Token for a new link, or None when nothing should be sent.

        Nothing is sent for an unknown, deactivated or already verified account,
        when verification is off, when a link was sent a moment ago, or when
        the account already got EMAIL_VERIFICATION_MAX_PER_DAY links within 24 hours.
        """
        if not settings.EMAIL_VERIFICATION_REQUIRED:
            return None

        user = await self._users.get_by_email(email)
        if not user or not user.is_active or user.is_verified:
            return None

        now = datetime.now(UTC)
        latest = await self._tokens.get_latest_for_user(user.id)
        cooldown = timedelta(seconds=settings.EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS)
        if latest and now - latest.created_at < cooldown:
            return None

        sent_today = await self._tokens.count_created_since(user.id, now - timedelta(days=1))
        if sent_today >= settings.EMAIL_VERIFICATION_MAX_PER_DAY:
            logger.warning("Daily limit of verification emails reached", user_id=str(user.id))
            return None

        logger.info("Email verification link requested again", user_id=str(user.id))
        return user, await self.create_link_token(user)

    async def verify(self, token: str) -> User:
        now = datetime.now(UTC)
        stored = await self._tokens.get_by_hash(hash_password_reset_token(token))
        if not stored or stored.used_at is not None or stored.expires_at <= now:
            raise InvalidVerificationTokenError

        user = await self._users.get_by_id(stored.user_id)
        if not user or not user.is_active:
            raise InvalidVerificationTokenError

        user.is_verified = True
        await self._users.update(user)
        await self._tokens.invalidate_for_user(user.id, now)
        logger.info("Email verified", user_id=str(user.id))
        return user
