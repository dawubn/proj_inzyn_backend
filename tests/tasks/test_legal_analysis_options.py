"""The legal analysis task follows the options chosen when the analysis was started."""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.enums.analysis import ProcessingStage, RedactionCategory
from app.enums.document import DocumentType
from tests.tasks.test_legal_analysis_classification import (
    _build_db_objects,
    _make_extraction,
    _make_ocr_result,
    _make_temp_pdf,
    _run_task_with_patches,
)


@pytest.fixture(autouse=True)
def _azure_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", "key")
    monkeypatch.setattr(settings, "AZURE_OPENAI_DEFAULT_MODEL", "gpt-4.1")


def _run(options: dict[str, Any] | None) -> tuple[MagicMock, dict[str, MagicMock]]:
    """Run the task for an analysis stored with the given options."""
    ocr_text = "Umowa najmu lokalu mieszkalnego"
    session = MagicMock()
    tmp_path = _make_temp_pdf()
    try:
        _, fake_analysis = _build_db_objects(session, tmp_path)
        if options is not None:
            fake_analysis.pipeline_params = {"options": options}

        extractor = MagicMock()
        extractor.extract.return_value = _make_extraction(DocumentType.CONTRACT, ocr_text, 0.7)
        classifier = MagicMock()
        classifier.classify.return_value = (DocumentType.CONTRACT, 0.9, {"contract": 0.9})

        mocks: dict[str, MagicMock] = {}
        result = _run_task_with_patches(
            _make_ocr_result(ocr_text),
            extractor,
            MagicMock(return_value=classifier),
            session,
            fake_analysis,
            mocks=mocks,
        )
        assert result["status"] == "success", result
        return fake_analysis, mocks
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


def test_analysis_without_options_runs_full_pipeline() -> None:
    analysis, mocks = _run(None)

    categories = mocks["redaction"].anonymize_file.call_args.kwargs["categories"]
    assert set(categories) == set(RedactionCategory)
    mocks["llm"].assert_called_once()
    assert mocks["llm"].call_args.args[2] == "gpt-4.1"
    assert analysis.legal_analysis_result["skipped"] is False
    assert analysis.legal_analysis_result["model"] == "gpt-4.1"


def test_selected_categories_are_passed_to_redaction() -> None:
    _, mocks = _run({"redaction_categories": ["pesel", "email"]})

    categories = mocks["redaction"].anonymize_file.call_args.kwargs["categories"]
    assert categories == [RedactionCategory.PESEL, RedactionCategory.EMAIL]


def test_anonymization_turned_off_masks_nothing() -> None:
    _, mocks = _run({"anonymization_enabled": False})

    assert mocks["redaction"].anonymize_file.call_args.kwargs["categories"] == []


def test_selected_model_is_used_for_ai_analysis() -> None:
    analysis, mocks = _run({"ai_model": "gpt-4o-mini"})

    assert mocks["llm"].call_args.args[2] == "gpt-4o-mini"
    assert analysis.legal_analysis_result["model"] == "gpt-4o-mini"


def test_no_model_is_recorded_when_azure_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", None)

    analysis, mocks = _run({"ai_model": "gpt-4o-mini"})

    mocks["llm"].assert_not_called()
    assert analysis.legal_analysis_result["model"] is None


def test_ai_analysis_turned_off_skips_the_model() -> None:
    analysis, mocks = _run({"ai_analysis_enabled": False})

    mocks["llm"].assert_not_called()
    assert analysis.legal_analysis_result == {
        "prompt": "",
        "summary": "",
        "errors": [],
        "applicable_laws": [],
        "skipped": True,
        "model": None,
    }
    assert analysis.processing_stage == ProcessingStage.COMPLETED
