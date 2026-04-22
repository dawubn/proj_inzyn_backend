import uuid

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.analysis_report import AnalysisReportResponse

router = APIRouter()


@router.get("/{analysis_id}", response_model=AnalysisReportResponse)
async def get_report(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
) -> AnalysisReportResponse:
    # TODO: implement via ReportService
    raise NotImplementedError
