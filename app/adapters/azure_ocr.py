"""
Azure AI Document Intelligence adapter.

Wraps the Azure SDK to provide a clean interface for the rest of the application.
Replace the TODO sections with real implementation once credentials are configured.
"""

import structlog
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

from app.core.config import settings
from app.core.exceptions import OCRServiceError

logger = structlog.get_logger(__name__)


class OCRResult:
    """Normalised result returned from any OCR provider."""

    def __init__(
        self,
        raw: dict,
        text_content: str,
        pages: int,
        confidence: float | None,
    ) -> None:
        self.raw = raw
        self.text_content = text_content
        self.pages = pages
        self.confidence = confidence


class AzureOCRAdapter:
    """Adapter for Azure AI Document Intelligence."""

    def __init__(self) -> None:
        self._client = DocumentIntelligenceClient(
            endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
        )

    def analyze_document(self, file_bytes: bytes, content_type: str) -> OCRResult:
        """
        Send document to Azure OCR and return normalised result.

        Args:
            file_bytes: Raw file content.
            content_type: MIME type of the document.

        Returns:
            OCRResult with raw API response and extracted text.

        Raises:
            OCRServiceError: When Azure returns an error.
        """
        log = logger.bind(content_type=content_type, size_bytes=len(file_bytes))
        log.info("Sending document to Azure OCR")

        try:
            # TODO: implement real Azure Document Intelligence call
            # Example:
            # poller = self._client.begin_analyze_document(
            #     "prebuilt-read",
            #     analyze_request={"base64Source": base64.b64encode(file_bytes).decode()},
            #     content_type="application/json",
            # )
            # result = poller.result()
            # return self._map_result(result)
            raise NotImplementedError("Azure OCR adapter not yet implemented")
        except NotImplementedError:
            raise
        except Exception as exc:
            log.error("Azure OCR failed", error=str(exc))
            raise OCRServiceError(f"Azure OCR error: {exc}") from exc

    def _map_result(self, azure_result: object) -> OCRResult:
        """Map Azure SDK result to internal OCRResult."""
        def get_value(source: object, name: str, default: object = None) -> object:
            if isinstance(source, dict):
                return source.get(name, default)
            return getattr(source, name, default)

        pages_data = get_value(azure_result, "pages", []) or []
        pages = len(pages_data)

        text_content = get_value(azure_result, "content", "") or ""
        if not text_content and pages_data:
            lines: list[str] = []
            for page in pages_data:
                for line in get_value(page, "lines", []) or []:
                    line_content = get_value(line, "content", "")
                    if line_content:
                        lines.append(str(line_content))
            text_content = "\n".join(lines)

        confidence = get_value(azure_result, "confidence")
        if confidence is None:
            total_confidence = 0.0
            confidence_count = 0

            for document in get_value(azure_result, "documents", []) or []:
                document_confidence = get_value(document, "confidence")
                if document_confidence is not None:
                    total_confidence += float(document_confidence)
                    confidence_count += 1

            if confidence_count == 0:
                for page in pages_data:
                    for word in get_value(page, "words", []) or []:
                        word_confidence = get_value(word, "confidence")
                        if word_confidence is not None:
                            total_confidence += float(word_confidence)
                            confidence_count += 1

            confidence = (
                total_confidence / confidence_count if confidence_count else None
            )
        else:
            confidence = float(confidence)

        if isinstance(azure_result, dict):
            raw = azure_result
        elif hasattr(azure_result, "as_dict"):
            raw = azure_result.as_dict()
        elif hasattr(azure_result, "to_dict"):
            raw = azure_result.to_dict()
        else:
            raw = {}

        return OCRResult(
            raw=raw,
            text_content=str(text_content),
            pages=pages,
            confidence=confidence,
        )
