from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.core.exceptions import ProcessingTimeoutError, UnsupportedFileTypeError
from app.db.session import get_db
from app.models.document_analysis import DocumentAnalysis
from app.models.user import User
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.schemas.document_analysis import DocumentAnalysisResponse, TriggerAnalysisResponse
from app.services.document import DocumentService
from app.services.document_analysis import DocumentAnalysisService
from app.services.redaction_service import RedactionService

logger = structlog.get_logger(__name__)

router = APIRouter()

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
)

# Common error responses for authenticated endpoints
COMMON_RESPONSES = {
    401: {"description": "Unauthorized - missing or invalid access token"},
    403: {"description": "Forbidden - insufficient permissions (admin only)"},
    404: {"description": "Not found - analysis/document does not exist or access denied"},
}


def _redaction_service() -> RedactionService:
    return RedactionService()


def _analysis_service(db: AsyncSession = Depends(get_db)) -> DocumentAnalysisService:
    return DocumentAnalysisService(DocumentAnalysisRepository(db), DocumentRepository(db))


def _document_service(db: AsyncSession = Depends(get_db)) -> DocumentService:
    return DocumentService(DocumentRepository(db))


async def _get_or_upload_document(
    document_id: uuid.UUID | None,
    file: UploadFile | None,
    current_user: User,
    doc_svc: DocumentService,
) -> uuid.UUID:
    """Get document_id from either existing document or uploaded file."""
    from app.schemas.document import DocumentCreate

    if document_id and file:
        raise ValueError("Cannot provide both document_id and file - use one or the other")

    if document_id:
        # Verify document exists and belongs to current user
        await doc_svc.get_or_raise(document_id, current_user.id)
        return document_id

    if file:
        content = await file.read()
        doc = await doc_svc.upload(
            owner_id=current_user.id,
            filename=file.filename or "unnamed",
            content=content,
            content_type=file.content_type or "application/octet-stream",
            metadata=DocumentCreate(),
        )
        return doc.id

    raise ValueError("Must provide either document_id or file")


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    responses={200: {"description": "Redacted file", "content": {"application/pdf": {}}}},
)  # type: ignore[misc]
async def redact_local_ocr(  # noqa: PLR0913
    background_tasks: BackgroundTasks,
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    svc: RedactionService = Depends(_redaction_service),
) -> FileResponse:
    """
    Inline local OCR (Tesseract) with redaction - process and return redacted file immediately.

    Accepts either:
    - document_id: existing document (reads from storage)
    - file: upload file

    Returns redacted file (HTTP 200) synchronously as binary content.
    """
    if document_id and file:
        raise ValueError("Cannot provide both document_id and file - use one or the other")

    if document_id:
        # Get existing document (verifying ownership)
        doc = await doc_svc.get_or_raise(document_id, current_user.id)

        # Read file from storage
        file_path = Path(doc.storage_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = file_path.read_bytes()
        original_filename = doc.original_filename
        content_type = doc.mime_type

    elif file:
        content_type = (file.content_type or "").lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            msg = (
                f"Unsupported type '{content_type}'. "
                "Accepted: application/pdf, image/png, image/jpeg."
            )
            raise UnsupportedFileTypeError(msg)
        content = await file.read()
        original_filename = file.filename or "document"

    else:
        raise ValueError("Must provide either document_id or file")

    # Write to temp file and process
    with tempfile.NamedTemporaryFile(
        suffix=Path(original_filename).suffix or ".bin", delete=False
    ) as tmp:
        tmp.write(content)
        input_path = Path(tmp.name)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(svc.anonymize_file, input_path, original_filename, content_type),
            timeout=settings.REDACTION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise ProcessingTimeoutError(
            f"Redaction exceeded the {settings.REDACTION_TIMEOUT_SECONDS}s time limit."
        ) from None
    finally:
        input_path.unlink(missing_ok=True)

    background_tasks.add_task(result.output_path.unlink, missing_ok=True)
    logger.info(
        "Local OCR redaction complete", filename=original_filename, user_id=str(current_user.id)
    )

    return FileResponse(
        path=str(result.output_path),
        media_type=result.media_type,
        filename=result.filename,
    )


@router.post(  # type: ignore[misc]
    "/local-ocr",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_local_ocr_async(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """
    Trigger local OCR (Tesseract) asynchronously and save results to DB.

    Accepts either:
    - document_id: existing document ID
    - file: upload file to process

    Returns immediately (HTTP 202) while processing happens in background.
    """
    from app.enums.analysis import AnalysisStatus
    from app.tasks.local_ocr import run_local_ocr_task

    # Get or upload document
    doc_id = await _get_or_upload_document(document_id, file, current_user, doc_svc)

    # Create and queue analysis
    analysis_repo = analysis_svc._analyses
    analysis = DocumentAnalysis(
        document_id=doc_id,
        status=AnalysisStatus.PENDING,
    )
    analysis = await analysis_repo.create(analysis)

    task = run_local_ocr_task.delay(str(analysis.id), str(doc_id))
    analysis.task_id = task.id
    analysis = await analysis_repo.update(analysis)
    await analysis_repo.session.commit()

    return TriggerAnalysisResponse(
        analysis_id=analysis.id,
        task_id=analysis.task_id or "",
        message="Local OCR queued",
    )


@router.post(  # type: ignore[misc]
    "/full-ocr",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_full_ocr(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """
    Trigger full OCR pipeline: local OCR → redaction → Azure OCR.

    Accepts either:
    - document_id: existing document ID
    - file: upload file to process

    Returns immediately (HTTP 202) while processing happens in background.
    """
    # Get or upload document
    doc_id = await _get_or_upload_document(document_id, file, current_user, doc_svc)

    # Trigger full pipeline
    analysis = await analysis_svc.trigger_analysis_with_redaction(doc_id, current_user.id)
    return TriggerAnalysisResponse(
        analysis_id=analysis.id,
        task_id=analysis.task_id or "",
        message="Full OCR pipeline queued",
    )


@router.post(  # type: ignore[misc]
    "/azure-ocr",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_azure_ocr(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """
    Trigger Azure OCR only (without local OCR or redaction).

    Accepts either:
    - document_id: existing document ID
    - file: upload file to process

    Returns immediately (HTTP 202) while processing happens in background.
    """
    # Get or upload document
    doc_id = await _get_or_upload_document(document_id, file, current_user, doc_svc)

    # Trigger Azure OCR only
    analysis = await analysis_svc.trigger_azure_ocr(doc_id, current_user.id)
    return TriggerAnalysisResponse(
        analysis_id=analysis.id,
        task_id=analysis.task_id or "",
        message="Azure OCR queued",
    )


@router.post(  # type: ignore[misc]
    "/legal-analysis",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_legal_analysis(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """
    Trigger full OCR pipeline with legal analysis.

    Pipeline: local OCR → redaction → Azure OCR → LLM legal analysis.

    Accepts either:
    - document_id: existing document ID
    - file: upload file to process

    Returns immediately (HTTP 202) while processing happens in background.
    Results include: document summary, errors/issues, and applicable laws.
    """
    # Get or upload document
    doc_id = await _get_or_upload_document(document_id, file, current_user, doc_svc)

    # Trigger legal analysis pipeline
    analysis = await analysis_svc.trigger_legal_analysis(doc_id, current_user.id)
    return TriggerAnalysisResponse(
        analysis_id=analysis.id,
        task_id=analysis.task_id or "",
        message="Legal analysis pipeline queued",
    )


@router.get(
    "",
    response_model=list[DocumentAnalysisResponse],
    responses={401: COMMON_RESPONSES[401]},
)  # type: ignore[misc]
async def list_redactions(
    current_user: User = Depends(get_current_user),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> list[DocumentAnalysisResponse]:
    """
    List all redactions/analyses for current user.
    """
    from sqlalchemy import select

    from app.models.document import Document

    # Query analyses joined with documents to filter by owner_id
    stmt = (
        select(DocumentAnalysis)
        .join(Document, DocumentAnalysis.document_id == Document.id)
        .where(Document.owner_id == current_user.id)
        .order_by(DocumentAnalysis.created_at.desc())
    )

    result = await analysis_svc._analyses.session.execute(stmt)
    analyses = list(result.scalars().all())

    return [DocumentAnalysisResponse.model_validate(a) for a in analyses]


@router.get(
    "/admin/all",
    response_model=list[DocumentAnalysisResponse],
    responses={401: COMMON_RESPONSES[401], 403: COMMON_RESPONSES[403]},
)  # type: ignore[misc]
async def list_all_redactions_admin(
    current_user: User = Depends(get_current_user),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> list[DocumentAnalysisResponse]:
    """
    List ALL redactions/analyses (admin only).
    """
    from app.core.exceptions import ForbiddenError
    from app.enums.analysis import UserRole

    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Only admins can access this endpoint")

    from sqlalchemy import select

    stmt = select(DocumentAnalysis).order_by(DocumentAnalysis.created_at.desc())
    result = await analysis_svc._analyses.session.execute(stmt)
    analyses = list(result.scalars().all())

    return [DocumentAnalysisResponse.model_validate(a) for a in analyses]


@router.get("/{analysis_id}", response_model=DocumentAnalysisResponse, responses=COMMON_RESPONSES)  # type: ignore[misc]
async def get_redaction(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentAnalysisService = Depends(_analysis_service),
) -> DocumentAnalysisResponse:
    """
    Get redaction/analysis status by ID. Only owner or admin can access.
    """
    from app.enums.analysis import UserRole

    # Admin can access any analysis, non-admin can only access their own
    owner_id = current_user.id if current_user.role != UserRole.ADMIN else None
    analysis = await svc.get_or_raise(analysis_id, owner_id)
    return DocumentAnalysisResponse.model_validate(analysis)


@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT, responses=COMMON_RESPONSES)  # type: ignore[misc]
async def delete_redaction(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a redaction/analysis by ID. Only document owner can delete.
    """
    from app.core.exceptions import NotFoundError
    from app.models.document import Document

    repo = DocumentAnalysisRepository(db)
    analysis = await repo.get_by_id(analysis_id)
    if not analysis:
        raise NotFoundError("Analysis not found")

    # Verify user owns the document
    doc = await db.get(Document, analysis.document_id)
    if not doc or doc.owner_id != current_user.id:
        raise NotFoundError("Analysis not found")

    await repo.delete(analysis)
