"""Unit tests for redaction detectors — no OCR engine required."""
from app.api.v1.redactions import ALLOWED_CONTENT_TYPES
from app.services.redaction_detectors import _PESEL_RE, find_personal_data


def _words(*texts: str) -> list[dict]:
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
    assert ALLOWED_CONTENT_TYPES == {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
    }
