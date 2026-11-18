"""Known anonymization leaks reproduced on recorded local OCR output."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import spacy

from app.services.redaction_detectors import find_personal_data

FIXTURES = Path(__file__).parents[1] / "fixtures" / "redaction"
CASES = sorted(path.name.removesuffix(".ocr.json") for path in FIXTURES.glob("*.ocr.json"))

needs_models = pytest.mark.skipif(
    not (spacy.util.is_package("pl_core_news_lg") and spacy.util.is_package("en_core_web_lg")),
    reason="spaCy models are not installed",
)


def _load(name: str) -> dict[str, Any]:
    case: dict[str, Any] = json.loads((FIXTURES / f"{name}.ocr.json").read_text(encoding="utf-8"))
    return case


def _detect(case: dict[str, Any]) -> set[tuple[int, int, str]]:
    return {(item["x"], item["y"], item["tekst"]) for item in find_personal_data(case["words"])}


def _detect_without_models(case: dict[str, Any]) -> set[tuple[int, int, str]]:
    with patch("app.services.redaction_detectors._load_spacy", return_value=False):
        return _detect(case)


def _key(word: dict[str, Any]) -> tuple[int, int, str]:
    return word["x"], word["y"], word["tekst"]


def _leaks(case: dict[str, Any], masked: set[tuple[int, int, str]], *, ner: bool) -> list[str]:
    return [
        f"{word['tekst']} ({word['label']})"
        for word in case["words"]
        if word.get("expect") == "mask"
        and not word.get("visual")
        and (ner or not word.get("needs_ner"))
        and _key(word) not in masked
    ]


def _hidden(case: dict[str, Any], masked: set[tuple[int, int, str]]) -> list[str]:
    return [
        word["tekst"]
        for word in case["words"]
        if word.get("expect") == "keep" and _key(word) in masked
    ]


def test_fixtures_cover_both_languages_and_variants() -> None:
    cases = [_load(name) for name in CASES]
    assert {case["language"] for case in cases} == {"pl", "en"}
    assert {case["variant"] for case in cases} == {"scan", "photo"}
    labels = {w["label"] for case in cases for w in case["words"] if w.get("expect") == "mask"}
    assert {"PESEL", "NIP", "NUMER RACHUNKU", "NUMER DOKUMENTU", "TELEFON", "EMAIL"} <= labels


@pytest.mark.parametrize("name", CASES)
def test_sensitive_values_are_masked_by_rules(name: str) -> None:
    case = _load(name)
    assert _leaks(case, _detect_without_models(case), ner=False) == []


@pytest.mark.parametrize("name", CASES)
def test_rules_keep_text_needed_for_analysis(name: str) -> None:
    case = _load(name)
    assert _hidden(case, _detect_without_models(case)) == []


@pytest.mark.slow
@needs_models
@pytest.mark.parametrize("name", CASES)
def test_sensitive_values_are_masked_with_language_models(name: str) -> None:
    case = _load(name)
    assert _leaks(case, _detect(case), ner=True) == []


@pytest.mark.slow
@needs_models
@pytest.mark.parametrize("name", CASES)
def test_language_models_keep_text_needed_for_analysis(name: str) -> None:
    case = _load(name)
    assert _hidden(case, _detect(case)) == []
