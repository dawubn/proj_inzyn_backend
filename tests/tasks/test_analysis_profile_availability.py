from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.schemas.analysis_report import ValidationIssue
from app.tasks.analysis import _add_profile_availability_warning


def test_adds_warning_when_validation_profile_is_missing() -> None:
    issues: list[ValidationIssue] = []

    is_available = _add_profile_availability_warning(
        issues,
        profile_name=None,
        rules=[],
        document_type=DocumentType.INVOICE,
    )

    assert is_available is False
    assert len(issues) == 1
    assert issues[0].rule_name == "Formal validation profile"
    assert issues[0].field_name == "document_type"
    assert issues[0].severity == ValidationSeverity.WARNING
    assert issues[0].message == "Formal validation is not available for this document type yet."
    assert issues[0].details == {
        "document_type": DocumentType.INVOICE.value,
        "reason": "unsupported_document_type",
    }


def test_does_not_add_warning_when_validation_profile_exists() -> None:
    issues: list[ValidationIssue] = []

    is_available = _add_profile_availability_warning(
        issues,
        profile_name="MVP - umowa",
        rules=[],
        document_type=DocumentType.CONTRACT,
    )

    assert is_available is True
    assert issues == []
