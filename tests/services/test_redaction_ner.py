"""Named entities from the HerBERT model; skipped where the model is not installed."""

from unittest.mock import patch

import pytest

from app.services import redaction_ner
from app.services.redaction_ner import find_entities

needs_herbert = pytest.mark.skipif(
    not redaction_ner.herbert_available(), reason="HerBERT NER model is not installed"
)


def _people(text: str, language: str = "pl") -> list[str]:
    return [text[a:b] for a, b, label in find_entities(text, language) if label == "IMIE/NAZWISKO"]


@pytest.mark.slow
@needs_herbert
def test_name_wrapped_onto_the_next_line_is_kept() -> None:
    people = _people("Pozwana Katarzyna\nZielińska-Wójcik nie stawiła się.")
    assert "Zielińska-Wójcik" in people


@pytest.mark.slow
@needs_herbert
def test_name_at_the_end_of_a_very_long_line_is_found() -> None:
    text = "Lorem ipsum dolor sit amet, " * 200 + "Spotkałem Jana Kowalskiego."
    assert any("Kowalskiego" in person for person in _people(text))


@pytest.mark.slow
@needs_herbert
def test_polish_name_on_an_english_page_is_found() -> None:
    text = "The parties met.\nKatarzyna Zielińska signed the contract.\nThe defendant agreed."
    assert any("Zielińska" in person for person in _people(text, "en"))


@pytest.mark.slow
@needs_herbert
def test_case_numbers_and_years_are_not_entities() -> None:
    text = "Sygn. akt I C 1234/23\nw sprawie II Ca 567/22\nHousing Act 1988"
    assert find_entities(text, "pl") == []


def test_titles_next_to_a_name_stay_visible() -> None:
    text = "Szanowny Pan\nAdwokat Beata Wróblewska"
    with patch.object(
        redaction_ner,
        "_load_herbert",
        return_value=_Fixed([(9, 12, "IMIE/NAZWISKO"), (13, 37, "IMIE/NAZWISKO")]),
    ):
        people = _people(text)
    assert people == ["Beata Wróblewska"]


class _Fixed:
    def __init__(self, spans: list[tuple[int, int, str]]) -> None:
        self._spans = spans

    def entities(self, text: str) -> list[tuple[int, int, str]]:
        return self._spans


def test_surname_that_is_also_an_office_stays_masked() -> None:
    text = "Jan Starosta i Maria Wojewoda"
    with patch.object(
        redaction_ner,
        "_load_herbert",
        return_value=_Fixed([(0, 12, "IMIE/NAZWISKO"), (15, 29, "IMIE/NAZWISKO")]),
    ):
        people = _people(text)
    assert people == ["Jan Starosta", "Maria Wojewoda"]
