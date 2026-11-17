from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from app.enums.analysis import RedactionCategory
from app.schemas.analysis_options import AnalysisOptions
from app.services import redaction_service
from app.services.redaction_detectors import find_personal_data
from app.services.redaction_service import RedactionService


def _words(*texts: str) -> list[dict[str, Any]]:
    return [
        {"tekst": t, "x": i * 120, "y": 0, "szerokosc": 100, "wysokosc": 20}
        for i, t in enumerate(texts)
    ]


SAMPLE = ("pesel:", "44051401458", "email:", "jan@firma.pl", "tel.", "601234567")


def _labels(items: list[dict[str, Any]]) -> set[str]:
    return {item["rodzaj_danych"] for item in items}


def test_default_options_run_the_full_pipeline() -> None:
    options = AnalysisOptions()

    assert options.anonymization_enabled
    assert set(options.redaction_categories) == set(RedactionCategory)
    assert options.ai_analysis_enabled
    assert options.ai_model is None


def test_options_are_normalized() -> None:
    options = AnalysisOptions(
        anonymization_enabled=False,
        redaction_categories=[RedactionCategory.PESEL],
        ai_analysis_enabled=False,
        ai_model="gpt-4.1",
    )

    assert options.redaction_categories == []
    assert options.ai_model is None


def test_duplicate_categories_are_removed() -> None:
    options = AnalysisOptions(
        redaction_categories=[
            RedactionCategory.EMAIL,
            RedactionCategory.PESEL,
            RedactionCategory.EMAIL,
        ]
    )

    assert options.redaction_categories == [RedactionCategory.PESEL, RedactionCategory.EMAIL]


def test_anonymization_needs_at_least_one_category() -> None:
    with pytest.raises(ValidationError):
        AnalysisOptions(anonymization_enabled=True, redaction_categories=[])


@pytest.mark.parametrize("params", [None, {}, {"options": "broken"}, {"options": {"x": 1}}])
def test_missing_or_broken_stored_options_fall_back_to_defaults(
    params: dict[str, Any] | None,
) -> None:
    assert AnalysisOptions.from_pipeline_params(params) == AnalysisOptions()


def test_stored_options_are_read_back() -> None:
    stored = AnalysisOptions(
        redaction_categories=[RedactionCategory.DATE], ai_model="gpt-4o-mini"
    ).model_dump(mode="json")

    options = AnalysisOptions.from_pipeline_params({"options": stored, "pending_task": {}})

    assert options.redaction_categories == [RedactionCategory.DATE]
    assert options.ai_model == "gpt-4o-mini"


def test_all_categories_are_detected_by_default() -> None:
    assert _labels(find_personal_data(_words(*SAMPLE))) == {
        "[UTAJNIONO: PESEL]",
        "[UTAJNIONO: EMAIL]",
        "[UTAJNIONO: TELEFON]",
    }


def test_only_selected_categories_are_detected() -> None:
    items = find_personal_data(_words(*SAMPLE), [RedactionCategory.EMAIL])

    assert _labels(items) == {"[UTAJNIONO: EMAIL]"}


def test_date_category_can_be_left_out() -> None:
    words = _words("data:", "27.01.1975", "pesel:", "44051401458")

    with_dates = find_personal_data(words, [RedactionCategory.DATE, RedactionCategory.PESEL])
    without_dates = find_personal_data(words, [RedactionCategory.PESEL])

    assert "[UTAJNIONO: DATA]" in _labels(with_dates)
    assert _labels(without_dates) == {"[UTAJNIONO: PESEL]"}


def test_no_categories_detect_nothing() -> None:
    assert find_personal_data(_words(*SAMPLE), []) == []


class _FakeOCR:
    def __init__(self) -> None:
        self.recognize: bool | None = None

    def ocr_image(
        self, _path: Any, recognize: bool = True
    ) -> tuple[list[list[dict[str, Any]]], list[Image.Image]]:
        self.recognize = recognize
        words = _words(*SAMPLE) if recognize else []
        return [words], [Image.new("RGB", (800, 40), "white")]


def _anonymize(
    monkeypatch: pytest.MonkeyPatch, categories: list[RedactionCategory] | None
) -> tuple[_FakeOCR, Image.Image]:
    ocr = _FakeOCR()
    monkeypatch.setattr(redaction_service, "LocalOCRAdapter", lambda: ocr)
    result = RedactionService().anonymize_file(
        input_path=None,  # type: ignore[arg-type]
        original_filename="scan.png",
        content_type="image/png",
        categories=categories,
    )
    try:
        with Image.open(result.output_path) as image:
            return ocr, image.convert("RGB").copy()
    finally:
        result.output_path.unlink()


def _has_masks(image: Image.Image) -> bool:
    # The page is white, so any black pixel comes from a mask
    darkest, _ = image.convert("L").getextrema()
    return bool(darkest == 0)


def test_anonymization_masks_detected_data(monkeypatch: pytest.MonkeyPatch) -> None:
    ocr, image = _anonymize(monkeypatch, None)

    assert ocr.recognize is True
    assert _has_masks(image)


def test_anonymization_turned_off_keeps_the_page_and_skips_local_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr, image = _anonymize(monkeypatch, [])

    assert ocr.recognize is False
    assert image.size == (800, 40)
    assert not _has_masks(image)
