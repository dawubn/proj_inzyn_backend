"""
Celery task: run full OCR pipeline + legal analysis via Azure LLM.

Steps:
1. Load document from DB
2. Run RedactionService (local OCR + sensitive data masking)
3. Save redacted file
4. Trigger Azure OCR on redacted file
5. Wait for Azure OCR results
6. Send OCR text to Azure LLM for legal analysis
7. Save legal analysis results to DB
"""

import re
import shutil
import uuid
from collections import defaultdict
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.enums.document import DocumentType
from app.tasks.celery_app import celery_app

if TYPE_CHECKING:
    from app.adapters.azure_ocr import OCRResult

logger = structlog.get_logger(__name__)


_MIN_POLYGON_COORDINATES = 8


def _extract_azure_words(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the legacy preview-word contract while sourcing every word from Azure.

    The pipeline sends TIFF images, for which Azure returns pixel coordinates.
    Unknown coordinate units are omitted rather than projected incorrectly.
    """
    words: list[dict[str, Any]] = []
    for index, page in enumerate(raw.get("pages") or []):
        if page.get("unit") != "pixel":
            continue
        width, height = page.get("width"), page.get("height")
        for word_index, word in enumerate(page.get("words") or []):
            polygon = word.get("polygon") or []
            if len(polygon) < _MIN_POLYGON_COORDINATES or not all(
                isinstance(v, int | float) for v in polygon
            ):
                continue
            xs, ys = polygon[::2], polygon[1::2]
            entry = {
                "id": f"az{len(words)}",
                "text": word.get("content", ""),
                "page": page.get("pageNumber", page.get("page_number", index + 1)),
                "source": "azure_ocr",
                "confidence": word.get("confidence"),
                "bbox": {
                    "x": min(xs),
                    "y": min(ys),
                    "width": max(xs) - min(xs),
                    "height": max(ys) - min(ys),
                },
            }
            if (
                isinstance(width, int | float)
                and isinstance(height, int | float)
                and width > 0
                and height > 0
            ):
                entry["nbbox"] = {
                    "x": min(xs) / width,
                    "y": min(ys) / height,
                    "width": (max(xs) - min(xs)) / width,
                    "height": (max(ys) - min(ys)) / height,
                }
            entry["line"] = _azure_word_line(word, page.get("lines") or [], word_index)
            words.append(entry)
    return words


def _azure_word_line(word: dict[str, Any], lines: list[dict[str, Any]], word_index: int) -> str:
    offset = (word.get("span") or {}).get("offset")
    if isinstance(offset, int):
        for index, line in enumerate(lines):
            for span in line.get("spans") or []:
                start = span.get("offset", 0)
                if start <= offset < start + span.get("length", 0):
                    return f"line-{index}"
    return f"word-{word_index}"


def _build_paged_text(ocr_result: "OCRResult") -> str:
    """Join OCR lines page by page with explicit page markers so the LLM can cite pages."""
    if not ocr_result.pages:
        return str(ocr_result.text_content)

    return "\n\n".join(
        f"[Strona {page.page_number}]\n" + "\n".join(page.lines) for page in ocr_result.pages
    )


_FUZZY_MATCH_THRESHOLD = 0.7
_TOKEN_RE = re.compile(r"\w+")


def _normalize_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _best_window_match(
    ref_tokens: list[str], words: list[dict[str, Any]]
) -> tuple[float, list[dict[str, Any]]]:
    """Find the run of consecutive words most similar to the reference phrase."""
    from difflib import SequenceMatcher

    window = len(ref_tokens)
    word_texts = [" ".join(_normalize_tokens(word["text"])) for word in words]
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(" ".join(ref_tokens))

    best_ratio = 0.0
    best_words: list[dict[str, Any]] = []
    for start in range(max(len(words) - window + 1, 1)):
        matcher.set_seq1(" ".join(word_texts[start : start + window]))
        floor = max(best_ratio, _FUZZY_MATCH_THRESHOLD)
        if matcher.real_quick_ratio() <= floor or matcher.quick_ratio() <= floor:
            continue
        ratio = matcher.ratio()
        if ratio > floor:
            best_ratio = ratio
            best_words = words[start : start + window]

    return best_ratio, best_words


def _map_error_to_bbox(
    text_reference: str, tesseract_words: list[dict[str, Any]], page: int | None = None
) -> dict[str, Any] | None:
    """
    Find the Tesseract bbox covering the error text_reference (fuzzy phrase matching).

    The page suggested by the LLM is searched first; other pages only if nothing matches there.
    Returns the id of the first matched word, the union bbox of the matched words and the page.
    """
    ref_tokens = _normalize_tokens(text_reference)
    if not ref_tokens or ref_tokens == ["brak"]:
        return None

    words_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in tesseract_words:
        if _normalize_tokens(word["text"]):
            words_by_page[word["page"]].append(word)

    matched: list[dict[str, Any]] = []
    if page in words_by_page:
        _, matched = _best_window_match(ref_tokens, words_by_page[page])
    if not matched:
        best_ratio = 0.0
        for page_num, page_words in words_by_page.items():
            if page_num == page:
                continue
            ratio, page_match = _best_window_match(ref_tokens, page_words)
            if ratio > best_ratio:
                best_ratio, matched = ratio, page_match
    if not matched:
        return None

    match: dict[str, Any] = {
        "id": matched[0]["id"],
        "bbox": _union_bbox([word["bbox"] for word in matched], digits=None),
        "page": matched[0]["page"],
    }
    if all("nbbox" in word for word in matched):
        # One normalized rect per text line, so multi-line references highlight as line strips
        match["rects"] = [
            _union_bbox([word["nbbox"] for word in line_words], digits=5)
            for _, line_words in groupby(matched, key=lambda word: word.get("line"))
        ]
    return match


def _union_bbox(boxes: list[dict[str, Any]], digits: int | None) -> dict[str, Any]:
    x0 = min(box["x"] for box in boxes)
    y0 = min(box["y"] for box in boxes)
    x1 = max(box["x"] + box["width"] for box in boxes)
    y1 = max(box["y"] + box["height"] for box in boxes)
    union = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
    if digits is None:
        return union
    return {key: round(value, digits) for key, value in union.items()}


def _warm_page_previews(
    file_path: Path, mime_type: str, analysis_id: str, log: structlog.stdlib.BoundLogger
) -> None:
    """Pre-render viewer previews; failures are not fatal, previews also render on demand."""
    from app.services.page_preview import PagePreviewService

    try:
        PagePreviewService(file_path, mime_type, analysis_id).warm()
    except Exception:
        log.exception("Page preview warm-up failed")


def _get_db_session() -> Session:
    """Create a synchronous DB session for Celery workers."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    return sessionmaker(bind=engine)()


def _build_legal_analysis_prompt(ocr_text: str, filename: str, page_count: int) -> str:
    """Build a structured prompt for Azure LLM legal analysis."""

    return f"""Jesteś prawnikiem specjalizującym się w prawie polskim. Przeanalizuj poniższy dokument:

Dokument: {filename}
Liczba stron: {page_count}
Początek każdej strony oznaczono znacznikiem [Strona N].

---
{ocr_text}
---

Proszę przeanalizować CAŁY dokument (wszystkie strony) z perspektywy prawa polskiego i udzielić:

1. **Podsumowanie**: Krótkie podsumowanie celu i zawartości całego dokumentu
2. **Błędy/Braki**: Co jest złego, brakuje lub jest nieprawidłowe w dokumencie, na każdej ze stron.
DLA KAŻDEGO BŁĘDU podaj dokładny fragment tekstu z dokumentu do którego się odnosi (text_reference)
oraz numer strony, na której występuje (page).
3. **Przepisy prawne**: Jakie przepisy, regulacje lub normy prawne mają zastosowanie do treści
wszystkich stron dokumentu

Odpowiedź sformatuj jako JSON z dokładnie tymi kluczami:
{{
  "summary": "...",
  "errors": [
    {{
      "issue": "opis błędu/braku",
      "text_reference": "dokładny fragment tekstu z dokumentu do którego się odnosi",
      "page": 1,
      "severity": "critical|high|medium"
    }},
    ...
  ],
  "applicable_laws": [
    {{"law": "nazwa przepisu", "description": "opis zastosowania",
      "reference": "artykuł/paragraf"}},
    ...
  ]
}}

WAŻNE:
- text_reference MUSI być dokładnym fragmentem z dokumentu, bez znaczników [Strona N]
- page to numer strony ze znacznika [Strona N]; dla braków wskaż stronę, na której
  dany element powinien się znaleźć
- Jeśli brakuje czegoś (np. daty), wpisz "brak" lub wskaż gdzie powinna być
- Odpowiadaj TYLKO poprawnym JSON, bez dodatkowego tekstu
- Dokument zawiera elementy utajnione (np. [UTAJNIONO]), które oznaczają usunięte dane wrażliwe. Nie uwzględniaj tego w analizie błędów."""


def _classify_document(
    text_content: str,
    fallback_type: "DocumentType",
    fallback_confidence: float,
    log: structlog.stdlib.BoundLogger,
) -> "tuple[DocumentType, float]":
    """Run TF-IDF + LR classifier; fall back to extraction result on failure."""
    if not text_content:
        return fallback_type, fallback_confidence

    from app.services.classification import ClassificationService

    try:
        document_type, confidence, _ = ClassificationService().classify(text_content)
        log.info(
            "Document classified",
            document_type=document_type,
            confidence=round(confidence, 4),
        )
    except Exception as exc:
        log.warning("Classification fallback used", reason=str(exc))
        return fallback_type, fallback_confidence
    else:
        return document_type, confidence


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.legal_analysis.run_legal_analysis_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_legal_analysis_task(self: Task, analysis_id: str, document_id: str) -> dict[str, Any]:
    """
    Complete OCR pipeline + legal analysis: local OCR, redaction, Azure OCR, LLM analysis.

    Steps:
    1. Load analysis + document from DB
    2. Run RedactionService (local OCR + masking)
    3. Save redacted file
    4. Trigger Azure OCR
    5. Retrieve OCR results
    6. Send to Azure LLM for legal analysis
    7. Save legal_analysis_result to DB
    """
    from app.enums.analysis import AnalysisStatus, ProcessingStage
    from app.models.document import Document
    from app.models.document_analysis import DocumentAnalysis
    from app.services.formal_validation import FormalDocumentExtractor
    from app.tasks.analysis import _coerce_document_type, _validate_and_save_report

    log = logger.bind(analysis_id=analysis_id, document_id=document_id, task_id=self.request.id)
    log.info("Legal analysis task started")

    session = _get_db_session()
    analysis = None

    try:
        # 1. Load analysis from DB
        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            log.warning("Analysis not found, retrying...")
            raise self.retry(exc=FileNotFoundError(f"Analysis {analysis_id} not found")) from None

        # 2. Set status to IN_PROGRESS - LOCAL OCR (step 1/4)
        analysis.status = AnalysisStatus.IN_PROGRESS
        analysis.processing_stage = ProcessingStage.LOCAL_OCR
        analysis.processing_step = 1
        session.commit()
        log.info("Status updated to LOCAL_OCR")

        # Load document
        document = session.get(Document, uuid.UUID(document_id))
        if not document:
            raise FileNotFoundError(f"Document {document_id} not found in DB")

        # Check file exists
        file_path = Path(document.storage_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # 3. Run RedactionService (local OCR + masking)
        from app.services.redaction_service import RedactionService

        redaction_svc = RedactionService()
        redaction_result = redaction_svc.anonymize_file(
            input_path=file_path,
            original_filename=document.original_filename,
            content_type=document.mime_type,
        )

        log.info(
            "Redaction completed",
            output_filename=redaction_result.filename,
            output_path=str(redaction_result.output_path),
        )

        # Update progress - REDACTION (step 2/4)
        analysis.processing_stage = ProcessingStage.REDACTION
        analysis.processing_step = 2
        session.commit()

        # 4. Save redacted TIFF as a per-analysis artifact alongside the original.
        #    Named by analysis_id so re-runs never collide and the original is never touched.
        from app.services.page_preview import redacted_path

        redacted_storage_path = redacted_path(file_path, analysis_id)
        shutil.move(str(redaction_result.output_path), str(redacted_storage_path))

        log.info("Redacted file saved", path=str(redacted_storage_path))

        # 6. Run Azure OCR on redacted file - AZURE OCR (step 3/4)
        analysis.processing_stage = ProcessingStage.AZURE_OCR
        analysis.processing_step = 3
        session.commit()

        from app.adapters.azure_ocr import AzureOCRAdapter

        adapter = AzureOCRAdapter()
        ocr_result = adapter.analyze_paged(redacted_storage_path.read_bytes(), "image/tiff")

        # 7. Save OCR results
        analysis.ocr_raw_result = ocr_result.raw
        tesseract_words = _extract_azure_words(ocr_result.raw)
        analysis.tesseract_words = tesseract_words  # Legacy column name; source is Azure.
        analysis.ocr_provider = ocr_result.provider
        session.commit()

        log.info("Azure OCR completed", page_count=ocr_result.page_count)

        extraction = FormalDocumentExtractor().extract(
            ocr_result.raw,
            fallback_document_type=_coerce_document_type(document.document_type),
        )

        # Classify document type using TF-IDF + Logistic Regression
        text_content = str(extraction.fields.get("document_text", "")).strip()
        document_type, classification_confidence = _classify_document(
            text_content, extraction.document_type, extraction.confidence, log
        )

        analysis.detected_document_type = document_type.value
        analysis.classification_confidence = classification_confidence
        analysis.extracted_fields = extraction.fields
        document.suggested_document_type = document_type

        _validate_and_save_report(
            session, analysis, document, document_type, classification_confidence
        )
        session.commit()

        # 8. Use OCR text for legal analysis - LLM ANALYSIS (step 4/4)
        analysis.processing_stage = ProcessingStage.LLM_ANALYSIS
        analysis.processing_step = 4
        session.commit()

        ocr_text = _build_paged_text(ocr_result)
        prompt = _build_legal_analysis_prompt(
            ocr_text, document.original_filename, ocr_result.page_count
        )

        # 9. Send to Azure LLM for legal analysis
        legal_analysis = _perform_legal_analysis(prompt, log)

        # 10. Map LLM errors to Azure word coordinates
        errors_with_bbox = []
        for error in legal_analysis.get("errors", []):
            error_copy = error.copy()
            text_ref = error.get("text_reference", "")
            if text_ref:
                bbox_match = _map_error_to_bbox(text_ref, tesseract_words, error.get("page"))
                if bbox_match:
                    error_copy["bbox"] = bbox_match["bbox"]
                    error_copy["word_id"] = bbox_match["id"]
                    error_copy["page"] = bbox_match["page"]
                    if bbox_match.get("rects"):
                        error_copy["locations"] = [
                            {"page": bbox_match["page"], "rects": bbox_match["rects"]}
                        ]
            errors_with_bbox.append(error_copy)

        # 11. Save legal analysis results and mark as completed
        analysis.legal_analysis_result = {
            "prompt": prompt,
            "summary": legal_analysis.get("summary", ""),
            "errors": errors_with_bbox,
            "applicable_laws": legal_analysis.get("applicable_laws", []),
        }
        analysis.status = AnalysisStatus.COMPLETED
        analysis.processing_stage = ProcessingStage.COMPLETED
        session.commit()

        _warm_page_previews(file_path, document.mime_type, analysis_id, log)

        log.info("Legal analysis completed and saved")

    except Exception as exc:
        log.exception("Legal analysis task failed")
        if analysis:
            analysis.status = AnalysisStatus.OCR_FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc) from exc

    else:
        return {"status": "success", "analysis_id": analysis_id}

    finally:
        session.close()


def _coerce_page(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _perform_legal_analysis(prompt: str, log: structlog.stdlib.BoundLogger) -> dict[str, Any]:
    """Send the legal analysis prompt to Azure LLM."""
    import json

    from openai import AzureOpenAI

    from app.core.config import settings

    if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_KEY:
        log.warning("Azure LLM not configured, skipping legal analysis")
        return {"summary": "", "errors": [], "applicable_laws": []}

    try:
        client = AzureOpenAI(
            api_key=settings.AZURE_OPENAI_KEY,
            api_version="2024-02-15-preview",
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        )

        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            response_format={"type": "json_object"},
        )

        response_text = response.choices[0].message.content or ""
        if not response_text:
            log.warning("LLM response was empty")
            return {"summary": "", "errors": [], "applicable_laws": []}

        log.info("LLM response received", length=len(response_text))

        # Parse JSON response
        result = json.loads(response_text)

        # Validate errors have text_reference
        errors = result.get("errors", [])
        validated_errors = []
        for error in errors:
            if isinstance(error, dict):
                validated_errors.append(
                    {
                        "issue": error.get("issue", ""),
                        "text_reference": error.get("text_reference", ""),
                        "page": _coerce_page(error.get("page")),
                        "severity": error.get("severity", "medium"),
                    }
                )
            else:
                # Handle case where error is just a string
                validated_errors.append(
                    {
                        "issue": str(error),
                        "text_reference": "",
                        "page": None,
                        "severity": "medium",
                    }
                )

        return {
            "summary": result.get("summary", ""),
            "errors": validated_errors,
            "applicable_laws": result.get("applicable_laws", []),
        }

    except Exception as exc:
        log.exception("LLM analysis failed", error=str(exc))
        return {
            "summary": "",
            "errors": [],
            "applicable_laws": [],
        }
