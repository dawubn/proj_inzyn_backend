from pydantic import EmailStr, Field

from app.enums.analysis import UserRole
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class UserCreate(OrmBase):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)
    role: UserRole = UserRole.BUSINESS_USER


class UserUpdate(OrmBase):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class UserResponse(UUIDSchema, TimestampSchema):
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
