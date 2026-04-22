from enum import StrEnum


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    OCR_IN_PROGRESS = "ocr_in_progress"
    OCR_COMPLETED = "ocr_completed"
    OCR_FAILED = "ocr_failed"
    CLASSIFYING = "classifying"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


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
