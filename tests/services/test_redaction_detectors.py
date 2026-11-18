"""Unit tests for redaction detectors — no OCR engine required."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from app.api.v1.redactions import ALLOWED_CONTENT_TYPES
from app.services.redaction_detectors import detect_document, find_personal_data
from app.services.redaction_rules import find_pattern_spans


def _words(*texts: str) -> list[dict[str, Any]]:
    return [
        {"tekst": t, "x": i * 120, "y": 0, "szerokosc": 100, "wysokosc": 20}
        for i, t in enumerate(texts)
    ]


@contextmanager
def _entities(*spans: tuple[int, int, str]) -> Iterator[None]:
    with (
        patch("app.services.redaction_detectors._load_spacy", return_value=True),
        patch("app.services.redaction_detectors.detect", return_value="pl"),
        patch("app.services.redaction_ner.find_entities", return_value=list(spans)),
    ):
        yield


def _labels(text: str) -> list[str]:
    return [label for _, _, label in find_pattern_spans(text)]


def test_pesel_re_matches_11_digits() -> None:
    assert _labels("44051401458") == ["PESEL"]


def test_pesel_re_no_match_12_digits() -> None:
    assert "PESEL" not in _labels("440514014580")


def test_pesel_matches_any_11_digits_no_checksum() -> None:
    assert _labels("12345678901") == ["PESEL"]


def test_detect_email() -> None:
    items = find_personal_data(_words("kontakt:", "jan@firma.pl"))
    assert any("EMAIL" in i.get("rodzaj_danych", "") for i in items)


def test_detect_pesel() -> None:
    items = find_personal_data(_words("pesel:", "44051401458"))
    assert any("PESEL" in i.get("rodzaj_danych", "") for i in items)


def test_detect_invalid_checksum_pesel_still_masked() -> None:
    items = find_personal_data(_words("numer:", "12345678901"))
    assert any("PESEL" in i.get("rodzaj_danych", "") for i in items)


@pytest.mark.parametrize(
    "texts",
    [("data:", "27.01.1975"), ("ur.", "27.01.1975"), ("ur.", "27", "stycznia", "1975")],
)
def test_dates_are_not_masked(texts: tuple[str, ...]) -> None:
    with patch("app.services.redaction_detectors._load_spacy", return_value=False):
        assert find_personal_data(_words(*texts)) == []


def test_allowed_content_types() -> None:
    assert {"application/pdf", "image/png", "image/jpeg", "image/jpg"} == ALLOWED_CONTENT_TYPES


def test_nlp_does_not_turn_dots_into_a_person_marker() -> None:
    words = _words("Jan", ".....", "opis", ".....")
    with _entities((0, 9, "IMIE/NAZWISKO")):
        items = find_personal_data(words)
    assert [item["tekst"] for item in items] == ["Jan"]


def test_thin_dotted_rule_misread_as_letters_is_covered_without_person_marker() -> None:
    words = _words("Formularz", "pełnomocnictwo", "ccc", "dalej", "dokument")
    words[2]["wysokosc"] = 3
    start = len("Formularz pełnomocnictwo ")
    with _entities((start, start + 3, "IMIE/NAZWISKO")):
        items = find_personal_data(words)
    assert len(items) == 1
    assert items[0]["rodzaj_danych"] == ""


@pytest.mark.parametrize("label", ["Mocodawca:", "Pełnomocnik:", "Petnomocnik:"])
def test_person_entity_cannot_mask_form_label_but_still_masks_name(label) -> None:
    words = _words(label, "Jan", "Kowalski")
    with _entities((0, len(label) + 13, "IMIE/NAZWISKO")):
        items = find_personal_data(words)
    assert [item["tekst"] for item in items] == ["Jan", "Kowalski"]


def test_known_name_is_not_masked_in_the_name_of_the_state() -> None:
    words = [
        {**w, "linia": "1"}
        for w in _words("prawu", "Rzeczypospolitej", "Polskiej.", "Pani", "Polska", "podpisuje")
    ]
    with patch("app.services.redaction_detectors._load_spacy", return_value=False):
        found = detect_document([words], {"polsk"})[0][0]
    assert [w["tekst"] for w in found] == ["Polska"]
