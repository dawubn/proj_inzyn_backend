import uuid
from typing import Any

import structlog

from app.core.exceptions import NotFoundError
from app.enums.analysis import ReportStatus, ValidationSeverity
from app.enums.document import DocumentType
from app.models.analysis_report import AnalysisReport
from app.repositories.analysis_report import AnalysisReportRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.services.formal_report import FormalValidationService
from app.services.validation import RuleEngineService

logger = structlog.get_logger(__name__)


class ReportService:
    def __init__(
        self,
        report_repo: AnalysisReportRepository,
        analysis_repo: DocumentAnalysisRepository,
        rule_engine: RuleEngineService,
    ) -> None:
        self._reports = report_repo
        self._analyses = analysis_repo
        self._engine = rule_engine

    async def build_report(self, analysis_id: uuid.UUID, rules: list[Any]) -> AnalysisReport:
        analysis = await self._analyses.get_by_id(analysis_id)
        if not analysis:
            raise NotFoundError("Analysis not found")

        fields = analysis.extracted_fields or {}
        issues, summary = FormalValidationService().build_report(
            rules,
            fields,
            "provided" if rules else None,
            DocumentType(analysis.detected_document_type or "unknown"),
        )

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
