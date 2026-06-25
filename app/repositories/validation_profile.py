import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums.document import DocumentType
from app.models.validation_profile import ValidationProfile
from app.repositories.base import BaseRepository


class ValidationProfileRepository(BaseRepository[ValidationProfile]):
    model = ValidationProfile

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_name(self, name: str) -> ValidationProfile | None:
        result = await self.session.execute(
            select(ValidationProfile).where(ValidationProfile.name == name)
        )
        return result.scalar_one_or_none()

    async def get_with_rules(self, profile_id: uuid.UUID) -> ValidationProfile | None:
        result = await self.session.execute(
            select(ValidationProfile)
            .options(selectinload(ValidationProfile.rules))
            .where(ValidationProfile.id == profile_id)
        )
        return result.scalar_one_or_none()

    async def list_with_rules(self) -> list[ValidationProfile]:
        result = await self.session.execute(
            select(ValidationProfile)
            .options(selectinload(ValidationProfile.rules))
            .order_by(ValidationProfile.document_type, ValidationProfile.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_by_document_type(
        self, document_type: DocumentType
    ) -> ValidationProfile | None:
        result = await self.session.execute(
            select(ValidationProfile)
            .options(selectinload(ValidationProfile.rules))
            .where(
                ValidationProfile.document_type == document_type,
                ValidationProfile.is_active.is_(True),
            )
            .order_by(ValidationProfile.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
