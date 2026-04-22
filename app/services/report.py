import uuid

import structlog

from app.core.exceptions import NotFoundError
from app.enums.analysis import ReportStatus, ValidationSeverity
from app.models.analysis_report import AnalysisReport
from app.models.document_analysis import DocumentAnalysis
from app.repositories.analysis_report import AnalysisReportRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.schemas.analysis_report import ValidationIssue
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

    async def build_report(self, analysis_id: uuid.UUID, rules: list) -> AnalysisReport:
        analysis = await self._analyses.get_by_id(analysis_id)
        if not analysis:
            raise NotFoundError("Analysis not found")

        fields = analysis.extracted_fields or {}
        issues = self._engine.run(rules, fields)

        errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
        warnings = [i for i in issues if i.severity == ValidationSeverity.WARNING]
        infos = [i for i in issues if i.severity == ValidationSeverity.INFO]

        score = self._compute_score(rules, errors)

        report = AnalysisReport(
            analysis_id=analysis_id,
            status=ReportStatus.COMPLETED,
            errors=[e.model_dump() for e in errors],
            warnings=[w.model_dump() for w in warnings],
            infos=[i.model_dump() for i in infos],
            error_count=len(errors),
            warning_count=len(warnings),
            is_complete=len(errors) == 0,
            completeness_score=score,
            summary={"total_rules": len(rules), "issues": len(issues)},
        )
        return await self._reports.create(report)

    def _compute_score(self, rules: list, errors: list[ValidationIssue]) -> float:
        if not rules:
            return 1.0
        return round(1.0 - len(errors) / len(rules), 4)
