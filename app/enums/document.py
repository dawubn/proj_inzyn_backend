from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    ARCHIVED = "archived"


class DocumentType(StrEnum):
    UNKNOWN = "unknown"
    LAWSUIT = "lawsuit"
    POWER_OF_ATTORNEY = "power_of_attorney"
    APPLICATION = "application"
    INVOICE = "invoice"
    CONTRACT = "contract"
    PATENT = "patent"
    DEMAND_LETTER = "demand_letter"


class FileExtension(StrEnum):
    PDF = "pdf"
    JPG = "jpg"
    JPEG = "jpeg"
    PNG = "png"
