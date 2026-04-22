import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis_report import AnalysisReport
from app.repositories.base import BaseRepository


class AnalysisReportRepository(BaseRepository[AnalysisReport]):
    model = AnalysisReport

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_analysis_id(self, analysis_id: uuid.UUID) -> AnalysisReport | None:
        result = await self.session.execute(
            select(AnalysisReport).where(AnalysisReport.analysis_id == analysis_id)
        )
        return result.scalar_one_or_none()
