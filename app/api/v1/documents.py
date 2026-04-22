import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.pagination import PaginationParams
from app.db.session import get_db
from app.models.user import User
from app.repositories.document import DocumentRepository
from app.schemas.common import PaginatedResponse
from app.schemas.document import DocumentCreate, DocumentResponse
from app.services.document import DocumentService

router = APIRouter()


def _document_service(db: AsyncSession = Depends(get_db)) -> DocumentService:
    return DocumentService(DocumentRepository(db))


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    description: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    svc: DocumentService = Depends(_document_service),
) -> DocumentResponse:
    content = await file.read()
    doc = await svc.upload(
        owner_id=current_user.id,
        filename=file.filename or "unnamed",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        metadata=DocumentCreate(description=description),
    )
    return DocumentResponse.model_validate(doc)


@router.get("", response_model=PaginatedResponse[DocumentResponse])
async def list_documents(
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(get_current_user),
    svc: DocumentService = Depends(_document_service),
) -> PaginatedResponse[DocumentResponse]:
    docs, total = await svc.list_for_owner(
        current_user.id, page=pagination.page, page_size=pagination.page_size
    )
    pages = -(-total // pagination.page_size)  # ceiling division
    return PaginatedResponse(
        items=[DocumentResponse.model_validate(d) for d in docs],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentService = Depends(_document_service),
) -> DocumentResponse:
    doc = await svc.get_or_raise(document_id, current_user.id)
    return DocumentResponse.model_validate(doc)
