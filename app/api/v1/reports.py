import uuid

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_db
from app.enums.analysis import AnalysisStatus, UserRole
from app.models.user import User
from app.repositories.analysis_report import AnalysisReportRepository
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.schemas.analysis_report import AnalysisReportResponse
from app.services.document_analysis import DocumentAnalysisService
from app.services.report_export import content_disposition, render_report_pdf, report_filename

router = APIRouter()


def _analysis_service(db: AsyncSession = Depends(get_db)) -> DocumentAnalysisService:
    return DocumentAnalysisService(DocumentAnalysisRepository(db), DocumentRepository(db))


def _report_repo(db: AsyncSession = Depends(get_db)) -> AnalysisReportRepository:
    return AnalysisReportRepository(db)


def _document_repo(db: AsyncSession = Depends(get_db)) -> DocumentRepository:
    return DocumentRepository(db)


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


@router.get(
    "/{analysis_id}/pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Analysis report as PDF"},
        401: {"description": "Unauthorized - missing or invalid access token"},
        404: {"description": "Not found - analysis does not exist or access denied"},
        409: {"description": "Analysis is not completed yet"},
    },
)  # type: ignore[misc]
async def download_report_pdf(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
    reports: AnalysisReportRepository = Depends(_report_repo),
    documents: DocumentRepository = Depends(_document_repo),
) -> Response:
    """Download the analysis report (formal validation and legal analysis) as a PDF file."""
    owner_id = current_user.id if current_user.role != UserRole.ADMIN else None
    analysis = await analysis_svc.get_or_raise(analysis_id, owner_id)
    if analysis.status != AnalysisStatus.COMPLETED:
        raise ConflictError("Analysis is not completed yet")

    document = await documents.get_by_id(analysis.document_id)
    if not document:
        raise NotFoundError("Document not found")
    report = await reports.get_by_analysis_id(analysis_id)

    pdf = await run_in_threadpool(render_report_pdf, document, analysis, report)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(report_filename(document.original_filename))
        },
    )
