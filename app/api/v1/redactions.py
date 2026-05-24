from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, status
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.api.dependencies.auth import get_current_user
from app.models.user import User
from app.services.redaction import RedactionResult, RedactionService

router = APIRouter()


def _redaction_service() -> RedactionService:
    return RedactionService()


@router.post("", status_code=status.HTTP_200_OK)  # type: ignore[misc]
async def redact_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    svc: RedactionService = Depends(_redaction_service),
) -> FileResponse:
    content = await file.read()
    result = svc.redact_file(
        filename=file.filename or "unnamed",
        content=content,
        content_type=file.content_type or "application/octet-stream",
    )
    return _file_response(result, svc)


def _file_response(result: RedactionResult, svc: RedactionService) -> FileResponse:
    headers = {
        "X-Redaction-Findings-Count": str(result.findings_count),
        "X-Redaction-Boxes-Count": str(result.redacted_boxes_count),
        "X-Redaction-Pages": str(result.pages_count),
        "X-Redaction-Types": ",".join(data_type.value for data_type in result.detected_types),
    }
    return FileResponse(
        path=Path(result.output_path),
        media_type=result.media_type,
        filename=result.output_filename,
        headers=headers,
        background=BackgroundTask(svc.cleanup, result.temp_dir),
    )
