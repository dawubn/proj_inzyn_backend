import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.schemas.document_analysis import DocumentAnalysisResponse, TriggerAnalysisResponse
from app.services.document_analysis import DocumentAnalysisService

router = APIRouter()


def _analysis_service(db: AsyncSession = Depends(get_db)) -> DocumentAnalysisService:
    return DocumentAnalysisService(DocumentAnalysisRepository(db), DocumentRepository(db))


@router.post("", response_model=TriggerAnalysisResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_analysis(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    analysis = await svc.trigger_analysis(document_id, current_user.id)
    return TriggerAnalysisResponse(
        analysis_id=analysis.id,
        task_id=analysis.task_id or "",
        message="Analysis queued",
    )


@router.get("/{analysis_id}", response_model=DocumentAnalysisResponse)
async def get_analysis(
    document_id: uuid.UUID,
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentAnalysisService = Depends(_analysis_service),
) -> DocumentAnalysisResponse:
    analysis = await svc.get_or_raise(analysis_id)
    return DocumentAnalysisResponse.model_validate(analysis)
