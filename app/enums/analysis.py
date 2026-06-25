from enum import StrEnum


class AnalysisStatus(StrEnum):
    # General states
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

    # Legacy/specific states (kept for backwards compatibility)
    OCR_IN_PROGRESS = "ocr_in_progress"
    OCR_COMPLETED = "ocr_completed"
    OCR_FAILED = "ocr_failed"
    CLASSIFYING = "classifying"
    VALIDATING = "validating"


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
