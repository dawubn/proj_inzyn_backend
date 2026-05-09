import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UUIDSchema(OrmBase):
    id: uuid.UUID


class TimestampSchema(OrmBase):
    created_at: datetime
    updated_at: datetime


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class MessageResponse(BaseModel):
    message: str
