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

# Common error responses for authenticated endpoints
COMMON_RESPONSES = {
    401: {"description": "Unauthorized - missing or invalid access token"},
    403: {"description": "Forbidden - insufficient permissions (admin only)"},
    404: {"description": "Not found - document does not exist or access denied"},
}


def _document_service(db: AsyncSession = Depends(get_db)) -> DocumentService:
    return DocumentService(DocumentRepository(db))


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)  # type: ignore[misc]
async def upload_document(
    file: UploadFile = File(...),
    description: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    svc: DocumentService = Depends(_document_service),
) -> DocumentResponse:
    """Upload document (PDF, PNG, JPG/JPEG).

    Returns document metadata with ID and status.
    Supports up to 20 MB files.
    """
    content = await file.read()
    doc = await svc.upload(
        owner_id=current_user.id,
        filename=file.filename or "unnamed",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        metadata=DocumentCreate(description=description),
    )
    return DocumentResponse.model_validate(doc)


@router.get("/{document_id}", response_model=DocumentResponse, responses=COMMON_RESPONSES)  # type: ignore[misc]
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DocumentService = Depends(_document_service),
) -> DocumentResponse:
    """Get document details by ID.

    Only document owner or admin can access.
    Returns document metadata including filename, size, MIME type, and timestamps.
    """
    from app.enums.analysis import UserRole

    # Admin can access any document, non-admin can only access their own
    owner_id = current_user.id if current_user.role != UserRole.ADMIN else None
    doc = await svc.get_or_raise(document_id, owner_id)
    return DocumentResponse.model_validate(doc)


@router.get(
    "/admin/all",
    response_model=PaginatedResponse[DocumentResponse],
    responses={401: COMMON_RESPONSES[401], 403: COMMON_RESPONSES[403]},
)  # type: ignore[misc]
async def list_all_documents_admin(
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DocumentResponse]:
    """List all documents across all users (admin only).

    Returns paginated list of all documents ordered by creation date (newest first).
    """
    from app.core.exceptions import ForbiddenError
    from app.enums.analysis import UserRole

    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Only admins can access this endpoint")

    repo = DocumentRepository(db)
    offset = (pagination.page - 1) * pagination.page_size
    docs, total = await repo.list_all(offset=offset, limit=pagination.page_size)
    pages = -(-total // pagination.page_size)
    return PaginatedResponse(
        items=[DocumentResponse.model_validate(d) for d in docs],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=COMMON_RESPONSES)  # type: ignore[misc]
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a document by ID.

    Only document owner can delete their documents.
    Deletes document and all associated analyses.
    """
    repo = DocumentRepository(db)
    doc = await repo.get_by_id(document_id)
    if not doc or doc.owner_id != current_user.id:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Document not found")

    await repo.delete(doc)
