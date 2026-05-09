from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.analysis import ValidationSeverity
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.validation_profile import ValidationProfile


class ValidationRule(BaseModel):
    __tablename__ = "validation_rules"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("validation_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(100), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    severity: Mapped[ValidationSeverity] = mapped_column(
        String(50), nullable=False, default=ValidationSeverity.ERROR
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    profile: Mapped[ValidationProfile] = relationship(back_populates="rules", lazy="noload")

    def __repr__(self) -> str:
        return f"<ValidationRule id={self.id} name={self.name} type={self.rule_type}>"
