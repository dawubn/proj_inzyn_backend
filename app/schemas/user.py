from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.enums.analysis import UserRole
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class UserCreate(OrmBase):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=255)
    surname: str = Field(min_length=1, max_length=255)
    company: str | None = Field(default=None, min_length=1, max_length=255)
    role: UserRole = UserRole.BUSINESS_USER


class UserUpdate(OrmBase):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    surname: str | None = Field(default=None, min_length=1, max_length=255)
    company: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class UserProfileUpdate(BaseModel):
    """Fields a user can change on their own account. Omitted fields stay unchanged."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    surname: str | None = Field(default=None, min_length=1, max_length=255)
    company: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("name", "surname")
    @classmethod
    def _not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("must not be null")
        return value


class UserResponse(UUIDSchema, TimestampSchema):
    email: str
    name: str
    surname: str | None
    company: str | None
    role: UserRole
    is_active: bool
    is_verified: bool
