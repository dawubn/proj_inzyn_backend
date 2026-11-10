from enum import StrEnum


class AnalysisStatus(StrEnum):
    # General states
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"

    # Legacy/specific states (kept for backwards compatibility)
    OCR_IN_PROGRESS = "ocr_in_progress"
    OCR_COMPLETED = "ocr_completed"
    OCR_FAILED = "ocr_failed"
    CLASSIFYING = "classifying"
    VALIDATING = "validating"


# Statusy zajmujące slot przetwarzania użytkownika.
# OCR_COMPLETED utrzymuje slot między OCR a kolejnym etapem.
# CANCELLING utrzymuje slot do potwierdzenia zakończenia pracy.
ACTIVE_STATUSES: frozenset[AnalysisStatus] = frozenset(
    {
        AnalysisStatus.IN_PROGRESS,
        AnalysisStatus.CANCELLING,
        AnalysisStatus.OCR_IN_PROGRESS,
        AnalysisStatus.OCR_COMPLETED,
        AnalysisStatus.CLASSIFYING,
        AnalysisStatus.VALIDATING,
    }
)


TERMINAL_STATUSES: frozenset[AnalysisStatus] = frozenset(
    {
        AnalysisStatus.COMPLETED,
        AnalysisStatus.FAILED,
        AnalysisStatus.OCR_FAILED,
        AnalysisStatus.CANCELLED,
    }
)


# Statusy, dla których można zgłosić anulowanie.
CANCELLABLE_STATUSES: frozenset[AnalysisStatus] = frozenset(
    {
        AnalysisStatus.PENDING,
        AnalysisStatus.QUEUED,
        *(status for status in ACTIVE_STATUSES if status != AnalysisStatus.CANCELLING),
    }
)


class ProcessingStage(StrEnum):
    # Detailed processing stages for granular status tracking.
    PENDING = "pending"
    LOCAL_OCR = "local_ocr"
    REDACTION = "redaction"
    AZURE_OCR = "azure_ocr"
    LLM_ANALYSIS = "llm_analysis"
    COMPLETED = "completed"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ReportStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class UserRole(StrEnum):
    ADMIN = "admin"
    BUSINESS_USER = "business_user"
