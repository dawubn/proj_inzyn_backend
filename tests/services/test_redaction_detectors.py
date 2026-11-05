"""Unit tests for redaction detectors — no OCR engine required."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.api.v1.redactions import ALLOWED_CONTENT_TYPES
from app.services.redaction_detectors import _PESEL_RE, find_personal_data


def _words(*texts: str) -> list[dict[str, Any]]:
    return [
        {"tekst": t, "x": i * 120, "y": 0, "szerokosc": 100, "wysokosc": 20}
        for i, t in enumerate(texts)
    ]


def test_pesel_re_matches_11_digits() -> None:
    assert _PESEL_RE.match("44051401458") is not None


def test_pesel_re_no_match_12_digits() -> None:
    assert _PESEL_RE.match("440514014580") is None


def test_pesel_matches_any_11_digits_no_checksum() -> None:
    assert _PESEL_RE.match("12345678901") is not None


def test_detect_email() -> None:
    items = find_personal_data(_words("kontakt:", "jan@firma.pl"))
    assert any("EMAIL" in i.get("rodzaj_danych", "") for i in items)


def test_detect_pesel() -> None:
    items = find_personal_data(_words("pesel:", "44051401458"))
    assert any("PESEL" in i.get("rodzaj_danych", "") for i in items)


def test_detect_invalid_checksum_pesel_still_masked() -> None:
    items = find_personal_data(_words("numer:", "12345678901"))
    assert any("PESEL" in i.get("rodzaj_danych", "") for i in items)


def test_detect_numeric_date() -> None:
    items = find_personal_data(_words("data:", "27.01.1975"))
    assert any("DATA" in i.get("rodzaj_danych", "") for i in items)


def test_detect_polish_date() -> None:
    items = find_personal_data(_words("ur.", "27", "stycznia", "1975"))
    assert any("DATA" in i.get("rodzaj_danych", "") for i in items)


def test_allowed_content_types() -> None:
    assert {"application/pdf", "image/png", "image/jpeg", "image/jpg"} == ALLOWED_CONTENT_TYPES


def test_date_match_masks_only_the_matched_occurrence() -> None:
    with patch("app.services.redaction_detectors._load_spacy", return_value=False):
        items = find_personal_data(_words("27", "stycznia", "1975", "Numer", "27"))
    assert {item["x"] for item in items} == {0, 120, 240}


def test_nlp_does_not_turn_dots_into_a_person_marker() -> None:
    words = _words("Jan", ".....", "opis", ".....")
    entity = SimpleNamespace(label_="persName", start_char=0, end_char=9)
    with (
        patch("app.services.redaction_detectors._load_spacy", return_value=True),
        patch("app.services.redaction_detectors.detect", return_value="pl"),
        patch(
            "app.services.redaction_detectors._nlp_pl", return_value=SimpleNamespace(ents=[entity])
        ),
    ):
        items = find_personal_data(words)
    assert [item["tekst"] for item in items] == ["Jan"]


def test_thin_dotted_rule_misread_as_letters_is_covered_without_person_marker() -> None:
    words = _words("Formularz", "pełnomocnictwo", "ccc", "dalej", "dokument")
    words[2]["wysokosc"] = 3
    start = len("Formularz pełnomocnictwo ")
    entity = SimpleNamespace(label_="persName", start_char=start, end_char=start + 3)
    with (
        patch("app.services.redaction_detectors._load_spacy", return_value=True),
        patch("app.services.redaction_detectors.detect", return_value="pl"),
        patch(
            "app.services.redaction_detectors._nlp_pl", return_value=SimpleNamespace(ents=[entity])
        ),
    ):
        items = find_personal_data(words)
    assert len(items) == 1
    assert items[0]["rodzaj_danych"] == ""


@pytest.mark.parametrize("label", ["Mocodawca:", "Pełnomocnik:", "Petnomocnik:"])
def test_person_entity_cannot_mask_form_label_but_still_masks_name(label) -> None:
    words = _words(label, "Jan", "Kowalski")
    entity = SimpleNamespace(label_="persName", start_char=0, end_char=len(label) + 13)
    with (
        patch("app.services.redaction_detectors._load_spacy", return_value=True),
        patch("app.services.redaction_detectors.detect", return_value="pl"),
        patch(
            "app.services.redaction_detectors._nlp_pl", return_value=SimpleNamespace(ents=[entity])
        ),
    ):
        items = find_personal_data(words)
    assert [item["tekst"] for item in items] == ["Jan", "Kowalski"]
