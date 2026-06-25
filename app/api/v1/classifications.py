import structlog
from fastapi import APIRouter, Depends, status

from app.api.dependencies.auth import get_current_user
from app.core.exceptions import ValidationError
from app.models.user import User
from app.schemas.classification import ClassifyRequest, ClassifyResponse
from app.services.classification import ClassificationService

logger = structlog.get_logger(__name__)

router = APIRouter()


def _classification_service() -> ClassificationService:
    return ClassificationService()


@router.post("", response_model=ClassifyResponse, status_code=status.HTTP_200_OK)  # type: ignore[misc]
async def classify_document(
    body: ClassifyRequest,
    current_user: User = Depends(get_current_user),
    svc: ClassificationService = Depends(_classification_service),
) -> ClassifyResponse:
    text = (body.text_content or "").strip()
    if not text and body.ocr_raw_result:
        text = str(body.ocr_raw_result.get("content", "")).strip()
    if not text:
        raise ValidationError(
            "Missing OCR content. Provide 'text_content'/'content' or ocr_raw_result.content"
        )

    document_type, confidence, all_scores = svc.classify(text)

    logger.info(
        "Classification requested",
        user_id=str(current_user.id),
        document_type=document_type,
        confidence=round(confidence, 4),
    )

    return ClassifyResponse(
        document_type=document_type,
        confidence=confidence,
        all_scores=all_scores,
    )
