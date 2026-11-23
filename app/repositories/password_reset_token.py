import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_token import PasswordResetToken
from app.repositories.base import BaseRepository


class PasswordResetTokenRepository(BaseRepository[PasswordResetToken]):
    model = PasswordResetToken

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_hash(self, token_hash: str) -> PasswordResetToken | None:
        result = await self.session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def get_latest_for_user(self, user_id: uuid.UUID) -> PasswordResetToken | None:
        result = await self.session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id)
            .order_by(PasswordResetToken.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_created_since(self, user_id: uuid.UUID, since: datetime) -> int:
        """How many links were created for the user since the given moment."""
        result = await self.session.execute(
            select(func.count())
            .select_from(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id, PasswordResetToken.created_at >= since)
        )
        return int(result.scalar_one())

    async def invalidate_for_user(self, user_id: uuid.UUID, used_at: datetime) -> None:
        """Mark every unused token of the user as used, so only the newest link works."""
        await self.session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
            .values(used_at=used_at)
        )
