import base64
from dataclasses import dataclass, field
from typing import Any

import structlog
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from app.core.config import settings
from app.core.exceptions import OCRServiceError

logger = structlog.get_logger(__name__)

# Azure model used for general document reading.
# Switch to "prebuilt-document" or a custom model ID if needed.
_AZURE_MODEL_ID = "prebuilt-read"


@dataclass
class OCRPage:
    """Extracted content of a single page."""

    page_number: int
    width: float
    height: float
    lines: list[str] = field(default_factory=list)
    words_confidence: list[float] = field(default_factory=list)


@dataclass
class OCRResult:
    """
    Normalised result returned from any OCR provider.

    Keeps raw Azure response alongside structured fields so downstream
    services (classifier, field extractor) can choose their source of truth.
    """

    raw: dict[str, Any]
    text_content: str
    pages: list[OCRPage]
    page_count: int
    confidence: float | None
    provider: str = "azure"

    @property
    def full_text(self) -> str:
        """Alias kept for backward compatibility."""
        return self.text_content


class AzureOCRAdapter:
    """
    Adapter for Azure AI Document Intelligence (prebuilt-read model).
    Usage:
        adapter = AzureOCRAdapter()
        result = adapter.analyze_document(file_bytes, "application/pdf")
    """

    def __init__(self) -> None:
        self._client = DocumentIntelligenceClient(
            endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
        )

    def analyze_document(self, file_bytes: bytes, content_type: str) -> OCRResult:
        """
        Send document bytes to Azure OCR and return a normalised OCRResult.

        Args:
            file_bytes: Raw file content (PDF / JPG / PNG).
            content_type: MIME type("application/pdf").

        Returns:
            OCRResult with structured content and raw API response.

        Raises:
            OCRServiceError: On any Azure API or network error.
        """
        log = logger.bind(content_type=content_type, size_bytes=len(file_bytes))
        log.info("Sending document to Azure OCR")

        try:
            encoded = base64.b64encode(file_bytes).decode("utf-8")
            request = AnalyzeDocumentRequest(bytes_source=encoded)

            poller = self._client.begin_analyze_document(
                _AZURE_MODEL_ID,
                request,
            )
            azure_result = poller.result()

        except (HttpResponseError, ServiceRequestError) as exc:
            log.exception("Azure OCR request failed", error=str(exc))
            raise OCRServiceError(f"Azure OCR error: {exc}") from exc
        except Exception as exc:
            log.exception("Unexpected error during Azure OCR")
            raise OCRServiceError(f"Unexpected OCR error: {exc}") from exc

        result = self._map_result(azure_result)
        log.info(
            "Azure OCR completed",
            page_count=result.page_count,
            confidence=result.confidence,
        )
        return result

    # ------------------------------------------------------------------
    # Internal mapping
    # ------------------------------------------------------------------

    def _map_result(self, azure_result: Any) -> OCRResult:
        """Map Azure SDK AnalyzeResult to internal OCRResult."""

        def _get(source: Any, name: str, default: Any = None) -> Any:
            if isinstance(source, dict):
                return source.get(name, default)
            return getattr(source, name, default)

        # text content
        text_content: str = str(_get(azure_result, "content", "") or "")

        # pages
        pages: list[OCRPage] = []
        pages_data = _get(azure_result, "pages", []) or []

        for raw_page in pages_data:
            lines = [
                str(_get(line, "content", ""))
                for line in (_get(raw_page, "lines", []) or [])
                if _get(line, "content")
            ]
            word_confidences = [
                float(_get(word, "confidence", 0.0))
                for word in (_get(raw_page, "words", []) or [])
                if _get(word, "confidence") is not None
            ]
            pages.append(
                OCRPage(
                    page_number=int(_get(raw_page, "page_number", len(pages) + 1)),
                    width=float(_get(raw_page, "width", 0.0)),
                    height=float(_get(raw_page, "height", 0.0)),
                    lines=lines,
                    words_confidence=word_confidences,
                )
            )

        # If Azure didn't return top-level content, reconstruct from pages
        if not text_content and pages:
            text_content = "\n".join(line for page in pages for line in page.lines)

        # confidence
        confidence: float | None = None

        # Try document level confidence first
        documents = _get(azure_result, "documents", []) or []
        doc_confidences = [
            float(_get(doc, "confidence"))
            for doc in documents
            if _get(doc, "confidence") is not None
        ]
        if doc_confidences:
            confidence = sum(doc_confidences) / len(doc_confidences)
        else:
            # Fall back to average word confidence
            all_word_confidences = [c for p in pages for c in p.words_confidence]
            if all_word_confidences:
                confidence = sum(all_word_confidences) / len(all_word_confidences)

        # raw dict
        if isinstance(azure_result, dict):
            raw: dict[str, Any] = azure_result
        elif hasattr(azure_result, "as_dict"):
            raw = azure_result.as_dict()
        else:
            raw = {}

        return OCRResult(
            raw=raw,
            text_content=text_content,
            pages=pages,
            page_count=len(pages),
            confidence=round(confidence, 4) if confidence is not None else None,
        )
