import uuid

from app.core.exceptions import NotFoundError
from app.enums.analysis import ReportStatus, ValidationSeverity
from app.models.analysis_report import AnalysisReport
from app.repositories.analysis_report import AnalysisReportRepository
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.repositories.validation_profile import ValidationProfileRepository
from app.services.formal_issue_locator import locate_formal_issues
from app.services.formal_report import FormalValidationService, resolve_document_type
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition


class ReportService:
    def __init__(
        self,
        report_repo: AnalysisReportRepository,
        analysis_repo: DocumentAnalysisRepository,
        document_repo: DocumentRepository,
        profile_repo: ValidationProfileRepository,
    ) -> None:
        self._reports = report_repo
        self._analyses = analysis_repo
        self._documents = document_repo
        self._profiles = profile_repo

    async def build_report(self, analysis_id: uuid.UUID) -> AnalysisReport:
        analysis = await self._analyses.get_by_id(analysis_id)
        if not analysis:
            raise NotFoundError("Analysis not found")

        document = await self._documents.get_by_id(analysis.document_id)
        effective, type_summary = resolve_document_type(
            document.document_type if document else None,
            analysis.detected_document_type,
            analysis.classification_confidence or 0.0,
        )
        extraction = FormalDocumentExtractor().extract(analysis.ocr_raw_result, effective)
        analysis.extracted_fields = extraction.fields
        profile = await self._profiles.get_active_by_document_type(effective)
        profile_name: str | None
        if profile:
            profile_name, rules = profile.name, sorted(profile.rules, key=lambda rule: rule.order)
        else:
            fallback = get_mvp_profile_definition(effective)
            profile_name = fallback.name if fallback else None
            rules = build_rule_models(fallback) if fallback else []
        issues, summary = FormalValidationService().build_report(
            rules, extraction.fields, profile_name, effective
        )
        issues = locate_formal_issues(issues, analysis.tesseract_words or [])
        summary.update(type_summary)

        errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
        warnings = [i for i in issues if i.severity == ValidationSeverity.WARNING]
        infos = [i for i in issues if i.severity == ValidationSeverity.INFO]

        score = summary["completeness_score"]

        report = AnalysisReport(
            analysis_id=analysis_id,
            status=ReportStatus.COMPLETED,
            errors=[e.model_dump() for e in errors],
            warnings=[w.model_dump() for w in warnings],
            infos=[i.model_dump() for i in infos],
            error_count=len(errors),
            warning_count=len(warnings),
            is_complete=summary["is_complete"],
            completeness_score=score,
            summary=summary,
        )
        return await self._reports.create(report)
