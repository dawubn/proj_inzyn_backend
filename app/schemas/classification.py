from typing import Any

from pydantic import AliasChoices, Field

from app.enums.document import DocumentType
from app.schemas.common import OrmBase


class ClassifyRequest(OrmBase):
    text_content: str | None = Field(
        default=None,
        validation_alias=AliasChoices("text_content", "content"),
    )
    ocr_raw_result: dict[str, Any] | None = None


class ClassifyResponse(OrmBase):

    document_type: DocumentType
    confidence: float
    all_scores: dict[str, float]


