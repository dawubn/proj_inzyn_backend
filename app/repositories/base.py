import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, record_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, record_id)

    async def get_all(self, *, offset: int = 0, limit: int = 20) -> list[ModelT]:
        result = await self.session.execute(select(self.model).offset(offset).limit(limit))
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(self.model))
        return result.scalar_one()

    async def create(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def _paginate(
        self, stmt: Select[Any], *, offset: int, limit: int
    ) -> tuple[list[ModelT], int]:
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        items = list(result.scalars().all())
        return items, total
