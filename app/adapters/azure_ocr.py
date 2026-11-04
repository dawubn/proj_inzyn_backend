import base64
import io
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF
import structlog
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from PIL import Image, ImageSequence

from app.core.config import settings
from app.core.exceptions import OCRServiceError

logger = structlog.get_logger(__name__)

# Azure model used for general document reading.
# Switch to "prebuilt-document" or a custom model ID if needed.
_AZURE_MODEL_ID = "prebuilt-read"

# Separator Azure itself puts between pages in the top-level "content"
_PAGE_SEPARATOR = "\n"
_MERGED_LIST_KEYS = ("pages", "paragraphs", "styles")


def _shift_positions(node: Any, char_offset: int, page_offset: int) -> Any:
    """Shift span offsets and page numbers of a raw Azure fragment by the given amounts."""
    if isinstance(node, list):
        return [_shift_positions(item, char_offset, page_offset) for item in node]
    if not isinstance(node, dict):
        return node

    shifted: dict[str, Any] = {}
    for key, value in node.items():
        if key == "offset" and isinstance(value, int):
            shifted[key] = value + char_offset
        elif key == "pageNumber" and isinstance(value, int):
            shifted[key] = value + page_offset
        else:
            shifted[key] = _shift_positions(value, char_offset, page_offset)
    return shifted


def _split_pdf(file_bytes: bytes, pages_per_chunk: int) -> tuple[list[bytes], int]:
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        page_count = len(doc)
        if page_count <= pages_per_chunk:
            return [file_bytes], page_count

        chunks: list[bytes] = []
        for start in range(0, page_count, pages_per_chunk):
            with fitz.open() as chunk:
                chunk.insert_pdf(doc, from_page=start, to_page=start + pages_per_chunk - 1)
                chunks.append(chunk.tobytes(garbage=3, deflate=True))
        return chunks, page_count


def _split_tiff(file_bytes: bytes, pages_per_chunk: int) -> tuple[list[bytes], int]:
    with Image.open(io.BytesIO(file_bytes)) as tiff:
        frames = [frame.copy() for frame in ImageSequence.Iterator(tiff)]
    if len(frames) <= pages_per_chunk:
        return [file_bytes], len(frames)

    chunks: list[bytes] = []
    for start in range(0, len(frames), pages_per_chunk):
        chunk_frames = frames[start : start + pages_per_chunk]
        buf = io.BytesIO()
        chunk_frames[0].save(
            buf,
            format="TIFF",
            save_all=True,
            append_images=chunk_frames[1:],
            compression="tiff_adobe_deflate",
        )
        chunks.append(buf.getvalue())
    return chunks, len(frames)


def split_into_page_chunks(
    file_bytes: bytes, content_type: str, pages_per_chunk: int
) -> tuple[list[bytes], int | None]:
    """
    Split a PDF or multi-page TIFF into files of at most pages_per_chunk pages.

    Returns the chunks and the document page count (None for single-image formats).
    pages_per_chunk <= 0 disables splitting.
    """
    if pages_per_chunk <= 0:
        return [file_bytes], None
    if content_type == "application/pdf":
        return _split_pdf(file_bytes, pages_per_chunk)
    if content_type in ("image/tiff", "image/tif"):
        return _split_tiff(file_bytes, pages_per_chunk)
    return [file_bytes], None


def _merge_raw_results(raws: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge raw Azure results of consecutive page chunks into a single document result."""
    merged: dict[str, Any] = {**raws[0], **{key: [] for key in _MERGED_LIST_KEYS}}
    contents: list[str] = []
    char_offset = 0
    page_offset = 0

    for raw in raws:
        for key in _MERGED_LIST_KEYS:
            merged[key].extend(_shift_positions(raw.get(key) or [], char_offset, page_offset))

        content = str(raw.get("content") or "")
        contents.append(content)
        char_offset += len(content) + len(_PAGE_SEPARATOR)
        page_offset += len(raw.get("pages") or [])

    merged["content"] = _PAGE_SEPARATOR.join(contents)
    return merged


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

    def analyze_paged(self, file_bytes: bytes, content_type: str) -> OCRResult:
        """
        Analyze every page of a multi-page document, sending it in chunks of
        AZURE_OCR_PAGES_PER_REQUEST pages and merging the results.

        Needed on the free tier, which reads only the first 2 pages of each request.
        Page numbers and text offsets in the merged raw result refer to the whole document.
        """
        chunks, page_count = split_into_page_chunks(
            file_bytes, content_type, settings.AZURE_OCR_PAGES_PER_REQUEST
        )
        if len(chunks) == 1:
            result = self.analyze_document(chunks[0], content_type)
        else:
            raws = [self.analyze_document(chunk, content_type).raw for chunk in chunks]
            result = self._map_result(_merge_raw_results(raws))

        if page_count is not None and result.page_count != page_count:
            logger.warning(
                "Azure OCR page count differs from document",
                page_count=result.page_count,
                document_page_count=page_count,
                chunks=len(chunks),
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
