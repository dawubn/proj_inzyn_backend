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
    INVOICE = "invoice"
    CONTRACT = "contract"
    ID_CARD = "id_card"
    PASSPORT = "passport"
    BANK_STATEMENT = "bank_statement"
    TAX_FORM = "tax_form"
    FINANCIAL_REPORT = "financial_report"
    GOVERNMENT_TENDER = "government_tender"
    LAW_AND_REGULATION = "law_and_regulation"
    MANUAL = "manual"
    PATENT = "patent"
    SCIENTIFIC_ARTICLE = "scientific_article"
    OTHER = "other"


class FileExtension(StrEnum):
    PDF = "pdf"
    JPG = "jpg"
    JPEG = "jpeg"
    PNG = "png"
