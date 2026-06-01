from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.core.exceptions import ProcessingTimeoutError, UnsupportedFileTypeError
from app.models.user import User
from app.services.redaction_service import RedactionService

logger = structlog.get_logger(__name__)

router = APIRouter()

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
)


def _redaction_service() -> RedactionService:
    return RedactionService()


@router.post("", status_code=status.HTTP_200_OK)
async def redact_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    svc: RedactionService = Depends(_redaction_service),
) -> FileResponse:
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedFileTypeError(
            f"Unsupported type '{content_type}'. Accepted: application/pdf, image/png, image/jpeg."
        )

    content = await file.read()
    original_filename = file.filename or "document"

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
    logger.info("Redaction complete", filename=original_filename, user_id=str(current_user.id))

    return FileResponse(
        path=str(result.output_path),
        media_type=result.media_type,
        filename=result.filename,
    )
