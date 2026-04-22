from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.document import DocumentType
from app.models.base import BaseModel


class ValidationProfile(BaseModel):
    __tablename__ = "validation_profiles"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[DocumentType] = mapped_column(String(50), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    rules: Mapped[list["ValidationRule"]] = relationship(
        back_populates="profile", lazy="noload", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ValidationProfile id={self.id} name={self.name} type={self.document_type}>"
