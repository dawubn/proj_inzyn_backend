import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.enums.analysis import UserRole
from app.models.user import User
from app.repositories.analysis_report import AnalysisReportRepository
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.schemas.analysis_report import AnalysisReportResponse
from app.services.document_analysis import DocumentAnalysisService

router = APIRouter()


def _analysis_service(db: AsyncSession = Depends(get_db)) -> DocumentAnalysisService:
    return DocumentAnalysisService(DocumentAnalysisRepository(db), DocumentRepository(db))


def _report_repo(db: AsyncSession = Depends(get_db)) -> AnalysisReportRepository:
    return AnalysisReportRepository(db)


@router.get("/{analysis_id}", response_model=AnalysisReportResponse)  # type: ignore[misc]
async def get_report(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
    reports: AnalysisReportRepository = Depends(_report_repo),
) -> AnalysisReportResponse:
    owner_id = current_user.id if current_user.role != UserRole.ADMIN else None
    await analysis_svc.get_or_raise(analysis_id, owner_id)

    report = await reports.get_by_analysis_id(analysis_id)
    if not report:
        raise NotFoundError("Analysis report not found")

    return AnalysisReportResponse.model_validate(report)
