from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.ai_models import AI_MODEL_PROFILES, TEST_DOCUMENT, TEST_ISSUES_TOTAL
from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.db.session import get_db
from app.enums.analysis import TERMINAL_STATUSES, RedactionCategory, UserRole
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.user import User
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.schemas.analysis_options import (
    ALL_REDACTION_CATEGORIES,
    AiModelOption,
    AiModelTestResult,
    AnalysisOptions,
    AnalysisOptionsResponse,
)
from app.schemas.document import DocumentCreate
from app.schemas.document_analysis import (
    DocumentAnalysisListResponse,
    DocumentAnalysisResponse,
    PageInfo,
    PagesManifestResponse,
    TriggerAnalysisResponse,
)
from app.services.document import DocumentService
from app.services.document_analysis import DocumentAnalysisService
from app.services.page_preview import PagePreviewService, PreviewSize, PreviewVariant

router = APIRouter()

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
)

COMMON_RESPONSES = {
    401: {"description": "Unauthorized - missing or invalid access token"},
    403: {"description": "Forbidden - insufficient permissions"},
    404: {"description": "Not found - resource does not exist or access denied"},
}

CONFLICT_RESPONSE = {
    409: {"description": "Processing state conflicts with the requested operation"},
}


def _analysis_service(db: AsyncSession = Depends(get_db)) -> DocumentAnalysisService:
    return DocumentAnalysisService(
        DocumentAnalysisRepository(db),
        DocumentRepository(db),
    )


def _document_service(db: AsyncSession = Depends(get_db)) -> DocumentService:
    return DocumentService(DocumentRepository(db))


async def _get_or_upload_document(
    document_id: uuid.UUID | None,
    file: UploadFile | None,
    current_user: User,
    doc_svc: DocumentService,
) -> uuid.UUID:
    """Resolve an owned document or upload a new one."""
    if document_id is not None and file is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot provide both document_id and file.",
        )

    if document_id is not None:
        await doc_svc.get_or_raise(document_id, current_user.id)
        return document_id

    if file is not None:
        content = await file.read()
        doc = await doc_svc.upload(
            owner_id=current_user.id,
            filename=file.filename or "unnamed",
            content=content,
            content_type=file.content_type or "application/octet-stream",
            metadata=DocumentCreate(),
        )
        return doc.id

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Must provide either document_id or file.",
    )


async def _trigger_response(
    analysis: DocumentAnalysis,
    svc: DocumentAnalysisService,
) -> TriggerAnalysisResponse:
    return TriggerAnalysisResponse(
        analysis_id=analysis.id,
        task_id=analysis.task_id,
        status=analysis.status,
        queue_position=await svc.get_queue_position(analysis),
        queued_at=analysis.queued_at,
        message="Waiting for analysis",
    )


def _ai_model_option(name: str) -> AiModelOption:
    profile = AI_MODEL_PROFILES.get(name)
    if profile is None:
        return AiModelOption(name=name)
    return AiModelOption(
        name=name,
        recommended=profile.recommended,
        description=profile.description,
        cost=profile.cost,
        speed=profile.speed,
        quality=profile.quality,
        test_result=AiModelTestResult(
            document=TEST_DOCUMENT,
            issues_found=profile.test_issues_found,
            issues_total=TEST_ISSUES_TOTAL,
            cost_usd=profile.test_cost_usd,
            seconds=profile.test_seconds,
        ),
    )


def _analysis_options(
    anonymize: bool = Query(True, description="Mask personal data before the document is sent on"),
    redaction_categories: list[RedactionCategory] | None = Query(
        None, description="Kinds of personal data to mask; all kinds when omitted"
    ),
    ai_analysis: bool = Query(True, description="Run the legal analysis with the AI model"),
    ai_model: str | None = Query(
        None, description="AI model for the legal analysis; the default one when omitted"
    ),
) -> AnalysisOptions:
    """Build the options of a new analysis from the request, rejecting unknown models."""
    if ai_analysis and ai_model is not None and ai_model not in settings.ai_models:
        raise ValidationError(f"AI model '{ai_model}' is not available")

    try:
        return AnalysisOptions(
            anonymization_enabled=anonymize,
            redaction_categories=(
                list(ALL_REDACTION_CATEGORIES)
                if redaction_categories is None
                else redaction_categories
            ),
            ai_analysis_enabled=ai_analysis,
            # Stored resolved, so the analysis keeps its model when the default changes
            ai_model=ai_model or settings.AZURE_OPENAI_DEFAULT_MODEL,
        )
    except ValueError as exc:
        raise ValidationError(
            "Select at least one data category or turn anonymization off"
        ) from exc


async def _detail_response(
    analysis: DocumentAnalysis,
    svc: DocumentAnalysisService,
) -> DocumentAnalysisResponse:
    response = DocumentAnalysisResponse.model_validate(analysis)
    response.queue_position = await svc.get_queue_position(analysis)
    return response


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    deprecated=True,
    responses={
        **COMMON_RESPONSES,
        409: {"description": "Inline processing disabled; use asynchronous processing"},
    },
)  # type: ignore[misc]
async def redact_local_ocr(
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """
    Inline processing is disabled to prevent bypassing the per-user queue.

    Use POST /redactions/local-ocr and poll GET /redactions/{analysis_id}.
    """
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Inline processing is disabled. Use /api/v1/redactions/local-ocr for queued processing."
        ),
    )


@router.post(  # type: ignore[misc]
    "/local-ocr",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: COMMON_RESPONSES[401], 404: COMMON_RESPONSES[404]},
)
async def trigger_local_ocr_async(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """Queue local OCR processing for an existing or uploaded document."""
    doc_id = await _get_or_upload_document(
        document_id,
        file,
        current_user,
        doc_svc,
    )
    analysis = await analysis_svc.trigger_local_ocr(doc_id, current_user.id)
    return await _trigger_response(analysis, analysis_svc)


@router.post(  # type: ignore[misc]
    "/full-ocr",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: COMMON_RESPONSES[401], 404: COMMON_RESPONSES[404]},
)
async def trigger_full_ocr(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """Queue local OCR, redaction and Azure OCR processing."""
    doc_id = await _get_or_upload_document(
        document_id,
        file,
        current_user,
        doc_svc,
    )
    analysis = await analysis_svc.trigger_analysis_with_redaction(
        doc_id,
        current_user.id,
    )
    return await _trigger_response(analysis, analysis_svc)


@router.post(  # type: ignore[misc]
    "/azure-ocr",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: COMMON_RESPONSES[401], 404: COMMON_RESPONSES[404]},
)
async def trigger_azure_ocr(
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """Queue Azure OCR processing without local OCR or redaction."""
    doc_id = await _get_or_upload_document(
        document_id,
        file,
        current_user,
        doc_svc,
    )
    analysis = await analysis_svc.trigger_azure_ocr(doc_id, current_user.id)
    return await _trigger_response(analysis, analysis_svc)


@router.post(  # type: ignore[misc]
    "/legal-analysis",
    response_model=TriggerAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: COMMON_RESPONSES[401], 404: COMMON_RESPONSES[404]},
)
async def trigger_legal_analysis(  # noqa: PLR0913 - FastAPI dependencies
    document_id: uuid.UUID | None = Query(None),
    file: UploadFile | None = File(None),
    options: AnalysisOptions = Depends(_analysis_options),
    current_user: User = Depends(get_current_user),
    doc_svc: DocumentService = Depends(_document_service),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> TriggerAnalysisResponse:
    """Queue local OCR, redaction, Azure OCR and legal processing.

    Without the option parameters the full pipeline runs: all kinds of personal data
    are masked and the legal analysis uses the default AI model.
    """
    doc_id = await _get_or_upload_document(
        document_id,
        file,
        current_user,
        doc_svc,
    )
    analysis = await analysis_svc.trigger_legal_analysis(doc_id, current_user.id, options)
    return await _trigger_response(analysis, analysis_svc)


@router.get(
    "/options",
    response_model=AnalysisOptionsResponse,
    responses={401: COMMON_RESPONSES[401]},
)  # type: ignore[misc]
async def get_analysis_options(
    _current_user: User = Depends(get_current_user),
) -> AnalysisOptionsResponse:
    """Describe what can be configured when an analysis is started."""
    return AnalysisOptionsResponse(
        redaction_categories=list(ALL_REDACTION_CATEGORIES),
        ai_analysis_available=settings.ai_analysis_available,
        ai_models=[_ai_model_option(name) for name in settings.ai_models],
        default_ai_model=settings.AZURE_OPENAI_DEFAULT_MODEL,
    )


@router.get(
    "",
    response_model=list[DocumentAnalysisListResponse],
    responses={401: COMMON_RESPONSES[401]},
)  # type: ignore[misc]
async def list_redactions(
    current_user: User = Depends(get_current_user),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> list[DocumentAnalysisListResponse]:
    """List the current user's processing requests."""
    stmt = (
        select(DocumentAnalysis)
        .join(Document, DocumentAnalysis.document_id == Document.id)
        .where(Document.owner_id == current_user.id)
        .order_by(DocumentAnalysis.created_at.desc())
    )
    result = await analysis_svc._analyses.session.execute(stmt)
    analyses = list(result.scalars().all())

    responses: list[DocumentAnalysisListResponse] = []
    for analysis in analyses:
        response = DocumentAnalysisListResponse.model_validate(analysis)
        response.queue_position = await analysis_svc.get_queue_position(analysis)
        responses.append(response)

    return responses


@router.get(
    "/admin/all",
    response_model=list[DocumentAnalysisResponse],
    responses={401: COMMON_RESPONSES[401], 403: COMMON_RESPONSES[403]},
)  # type: ignore[misc]
async def list_all_redactions_admin(
    current_user: User = Depends(get_current_user),
    analysis_svc: DocumentAnalysisService = Depends(_analysis_service),
) -> list[DocumentAnalysisResponse]:
    """List processing requests across all users; admin only."""
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Only admins can access this endpoint")

    stmt = select(DocumentAnalysis).order_by(DocumentAnalysis.created_at.desc())
    result = await analysis_svc._analyses.session.execute(stmt)
    analyses = list(result.scalars().all())

    responses: list[DocumentAnalysisResponse] = []
    for analysis in analyses:
        responses.append(await _detail_response(analysis, analysis_svc))

    return responses


@router.post(
    "/{analysis_id}/cancel",
    response_model=DocumentAnalysisResponse,
    status_code=status.HTTP_200_OK,
    responses={**COMMON_RESPONSES, **CONFLICT_RESPONSE},
)  # type: ignore[misc]
async def cancel_redaction(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentAnalysisService = Depends(_analysis_service),
) -> DocumentAnalysisResponse:
    """
    Cancel waiting processing or request cancellation of running processing.

    Running processing retains its slot until a worker confirms cancellation.
    Only the owner or an admin can cancel processing.
    """
    owner_id = None if current_user.role == UserRole.ADMIN else current_user.id

    analysis = await svc.cancel_processing(analysis_id, owner_id)
    return await _detail_response(analysis, svc)


@router.get(
    "/{analysis_id}",
    response_model=DocumentAnalysisResponse,
    responses=COMMON_RESPONSES,
)  # type: ignore[misc]
async def get_redaction(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentAnalysisService = Depends(_analysis_service),
) -> DocumentAnalysisResponse:
    """Get processing status and results; owner or admin only."""
    owner_id = None if current_user.role == UserRole.ADMIN else current_user.id

    analysis = await svc.get_or_raise(analysis_id, owner_id)
    return await _detail_response(analysis, svc)


async def _preview_service(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PagePreviewService:
    """Previews of an analysis the current user may access (owner or admin)."""
    svc = DocumentAnalysisService(
        DocumentAnalysisRepository(db),
        DocumentRepository(db),
    )
    owner_id = None if current_user.role == UserRole.ADMIN else current_user.id
    analysis = await svc.get_or_raise(analysis_id, owner_id)

    doc = await db.get(Document, analysis.document_id)
    if not doc:
        raise NotFoundError("Document not found")

    return PagePreviewService(
        Path(doc.storage_path),
        doc.mime_type,
        str(analysis.id),
    )


@router.get(
    "/{analysis_id}/pages",
    response_model=PagesManifestResponse,
    responses=COMMON_RESPONSES,
)  # type: ignore[misc]
async def get_pages_manifest(
    previews: PagePreviewService = Depends(_preview_service),
) -> PagesManifestResponse:
    """
    List document pages with their dimensions and the available preview variants.

    Coordinates in legal analysis error `locations` are relative (0-1) to these pages.
    """
    page_sizes = await asyncio.to_thread(previews.page_sizes)
    return PagesManifestResponse(
        variants=previews.available_variants(),
        pages=[PageInfo(page=p.page, width=p.width, height=p.height) for p in page_sizes],
    )


@router.get(
    "/{analysis_id}/pages/{page}/image",
    response_class=FileResponse,
    responses={
        **COMMON_RESPONSES,
        200: {"description": "Page preview", "content": {"image/webp": {}}},
    },
)  # type: ignore[misc]
async def get_page_image(
    page: int,
    variant: PreviewVariant = Query("redacted"),
    size: PreviewSize = Query("lg"),
    previews: PagePreviewService = Depends(_preview_service),
) -> FileResponse:
    """WebP preview of a single page (1-based); rendered on first request, then cached."""
    path = await asyncio.to_thread(previews.get_preview, variant, page, size)
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete(
    "/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**COMMON_RESPONSES, **CONFLICT_RESPONSE},
)  # type: ignore[misc]
async def delete_redaction(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete finished processing; only the document owner can delete."""
    repo = DocumentAnalysisRepository(db)

    analysis = await repo.get_by_id(analysis_id)
    if not analysis:
        raise NotFoundError("Analysis not found")

    doc = await db.get(Document, analysis.document_id)
    if not doc or doc.owner_id != current_user.id:
        raise NotFoundError("Analysis not found")

    await repo.lock_owner(analysis.owner_id)

    locked_analysis = await repo.get_by_id_for_update(analysis_id)
    if not locked_analysis:
        raise NotFoundError("Analysis not found")

    if locked_analysis.status not in TERMINAL_STATUSES:
        raise ConflictError("Cancel processing and wait for it to stop before deleting.")

    PagePreviewService(
        Path(doc.storage_path),
        doc.mime_type,
        str(locked_analysis.id),
    ).delete()

    await repo.delete(locked_analysis)
