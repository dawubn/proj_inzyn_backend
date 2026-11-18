"""Text rules for personal data, without OCR or language models."""

import pytest

from app.services.redaction_rules import (
    caption_label,
    find_caption_spans,
    find_pattern_spans,
    fold,
    is_label_word,
    is_title_word,
    name_stem,
    nip_valid,
    scrub_text,
)


def _found(text: str) -> list[tuple[str, str]]:
    spans = sorted(find_pattern_spans(text) + find_caption_spans(text.split("\n")))
    return [(label, text[start:end]) for start, end, label in spans]


def _labels(text: str) -> set[str]:
    return {label for label, _ in _found(text)}


def test_fold_keeps_offsets() -> None:
    text = "Zażółć GĘŚLĄ jaźń"
    assert len(fold(text)) == len(text)
    assert fold(text) == "zazolc gesla jazn"


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        ("PESEL 850101 12345", "PESEL", "850101 12345"),
        ("tel. +48 601234 567", "TELEFON", "+48 601234 567"),
        ("kontakt: jan.kowalski©example.com", "EMAIL", "jan.kowalski©example.com"),
        (
            "nr PL 611090 10140000 07121981 2874",
            "NUMER RACHUNKU",
            "PL 611090 10140000 07121981 2874",
        ),
        ("NIP 526-104-08-28", "NIP", "526-104-08-28"),
        ("REGON 012345678", "REGON", "012345678"),
        ("KRS 0000123456", "KRS", "0000123456"),
        ("dowodem osobistym TST 000000", "NUMER DOKUMENTU", "TST 000000"),
        ("księga wieczysta PO1P/00012345/6", "NUMER KW", "PO1P/00012345/6"),
        ("o numerze rejestracyjnym PO 12345", "NUMER REJESTRACYJNY", "PO 12345"),
        ("zam. ul. Kwiatowa 15/3, 02-495 Warszawa", "ADRES", "ul. Kwiatowa 15/3, 02-495 Warszawa"),
        ("os. Słoneczne 4 m. 12, 61-123 Poznań", "ADRES", "os. Słoneczne 4 m. 12, 61-123 Poznań"),
        (
            "Address: 221B Baker Street, London NW1 6XE",
            "ADRES",
            "221B Baker Street, London NW1 6XE",
        ),
        (
            "of 742 Evergreen Terrace, Springfield, IL 62704",
            "ADRES",
            "742 Evergreen Terrace, Springfield, IL 62704",
        ),
        ("SSN: 078-05-1120", "NUMER IDENTYFIKACYJNY", "078-05-1120"),
        ("NINO AB 12 34 56 C", "NUMER IDENTYFIKACYJNY", "AB 12 34 56 C"),
        ("IBAN GB82 WEST 1234 5698 7654 32", "NUMER RACHUNKU", "GB82 WEST 1234 5698 7654 32"),
        ("card 4111 1111 1111 1111", "NUMER KARTY", "4111 1111 1111 1111"),
        ("or by phone +1 (555) 123-4567.", "TELEFON", "+1 (555) 123-4567"),
    ],
)
def test_identifiers_and_contacts(text: str, label: str, value: str) -> None:
    assert (label, value) in _found(text)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Powód: JAN KOWALSKI", "JAN KOWALSKI"),
        ("Pozwana: Anna Nowak-Wiśniewska", "Anna Nowak-Wiśniewska"),
        ("Ja, niżej podpisana Anna Przykładowa", "Anna Przykładowa"),
        ("Ustanawiam pełnomocnikiem Jakuba Przykładowego", "Jakuba Przykładowego"),
        ("Korespondencję kierować do Pani Przykładowej", "Przykładowej"),
        ("Tenant: Mary O'Connor-Lee", "Mary O'Connor-Lee"),
        ("The Tenant, Ms O'Connor-Lee, agrees", "O'Connor-Lee"),
    ],
)
def test_people_after_captions_and_honorifics(text: str, value: str) -> None:
    assert ("IMIE/NAZWISKO", value) in _found(text)


@pytest.mark.parametrize(
    "text",
    [
        "UMOWA NAJMU LOKALU MIESZKALNEGO",
        "Sąd Rejonowy w Testowie",
        "Wartość przedmiotu sporu: 4 500,00 zł",
        "Razem do zapłaty: 1 230,00 zł",
        "FAKTURA VAT nr FV/123/2026",
        "Sygnatura akt: I C 123/26",
        "Usługa doradcza 1 szt. 1 000,00 zł netto, VAT 23%",
        "Sprzedawca: Testowa Firma Alfa Sp. z 0.0.",
        "Mocodawca: dane zostaną uzupełnione później",
        "Wynajmujący                       Najemca",
        "§ 4. Najemca parkuje pojazd na parkingu",
        "Termin płatności: 14 dni od doręczenia",
        "art. 471 k.c. oraz Dz.U. 2020 poz. 1740",
    ],
)
def test_text_needed_for_analysis_is_left_alone(text: str) -> None:
    assert _found(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "zawarta w dniu 12.03.2026 r.",
        "Testowo, 12 maja 2026 r.",
        "pismo z 5 V 2020",
        "od marca 2021 r.",
        "dnia pierwszego maja dwa tysiące dwudziestego roku",
        "ustawy z dnia 23 kwietnia 1964 r. Kodeks cywilny",
        "Data wystawienia: 01.10.2026",
        "made on March 12, 2026 between",
        "Dates: 03/04/2021, 2021-03-04, 4th Jul 2020, Jan. 5, 2021",
        "urodzona 12 maja 1988 r.",
        "ur. 5 V 1980",
        "urodzony dnia 12.XI.1979 r.",
        "urodził się w 1985 roku",
        "urodzona dnia pierwszego maja tysiąc dziewięćset osiemdziesiątego roku",
        "Data urodzenia: 12.03.1990",
        "born on 1st of February 1990 in",
        "Date of birth: March 12, 1990",
    ],
)
def test_dates_stay_visible(text: str) -> None:
    assert _found(text) == []


def test_empty_date_caption_does_not_take_the_next_paragraph() -> None:
    assert _found("Data zawarcia:\n§ 1. Strony umowy") == []


def test_caption_value_on_next_line_must_be_a_name() -> None:
    assert _found("Nabywca:\nUsługa doradcza 1 szt.") == []
    assert ("IMIE/NAZWISKO", "Katarzyna Wójcik") in _found("Nabywca:\nKatarzyna Wójcik")


def test_email_caption_is_not_treated_as_address() -> None:
    assert _labels("adres e-mail: anna@example.invalid") == {"EMAIL"}


def test_misread_heading_is_not_a_title_before_a_name() -> None:
    assert _found("KANCELARIA ADW OKACKA") == []
    assert ("IMIE/NAZWISKO", "Jan Nowak") in _found("Sprawę prowadzi adw. Jan Nowak")


def test_title_words() -> None:
    assert is_title_word("Pan")
    assert is_title_word("Adwokat")
    assert is_title_word("Mecenasa")
    assert is_title_word("SSR")
    assert is_title_word("sekr.")
    assert not is_title_word("Kowalski")
    assert not is_title_word("Starosta")
    assert not is_title_word("Wojewoda")
    assert not is_title_word("J.")


def test_role_ending_a_sentence_is_not_a_caption() -> None:
    line = "Administratorem danych jest Zleceniodawca. Inspektor Ochrony Danych jest dostępny."
    assert find_caption_spans([line]) == []


def test_job_title_after_role_is_not_a_name() -> None:
    assert find_caption_spans(["Zleceniodawca: Kierownik Działu Administracji"]) == []
    assert ("IMIE/NAZWISKO", "Jan Nowak") in _found("Zleceniodawca: Jan Nowak")


def test_email_caption_value_ends_at_comma() -> None:
    found = find_caption_spans(["e-mail: anna@example.invalid, rachunek 61 1090 1014"])
    assert [label for _, _, label in found] == ["EMAIL", "NUMER RACHUNKU"]
    assert found[0][1] == len("e-mail: anna@example.invalid")


def test_nip_checksum() -> None:
    assert nip_valid("526-104-08-28")
    assert not nip_valid("526-104-08-29")


def test_name_stems_join_inflected_forms() -> None:
    forms = ["Kowalski", "KOWALSKI", "Kowalskiego", "Kowalskim"]
    assert len({name_stem(form) for form in forms}) == 1
    assert name_stem("Przykładowa") == name_stem("Przykładowej") == name_stem("Przykładowego")
    assert name_stem("Pani") is None
    assert name_stem("Al") is None


def test_label_words_are_recognised() -> None:
    assert is_label_word("PESEL:")
    assert is_label_word("Pozwana:")
    assert is_label_word("Address:")
    assert not is_label_word("Kowalski")


@pytest.mark.parametrize(
    ("caption", "label"),
    [
        ("Imię i nazwisko", "IMIE/NAZWISKO"),
        ("Adres zamieszkania", "ADRES"),
        ("Seria i numer dowodu", "NUMER DOKUMENTU"),
        ("Telefon", "TELEFON"),
        ("Data", None),
        ("Data urodzenia", None),
        ("Wartość przedmiotu sporu", None),
    ],
)
def test_caption_label(caption: str, label: str | None) -> None:
    assert caption_label(caption) == label


def test_scrub_text_replaces_identifiers_and_known_names() -> None:
    text = "Powód Kowalskiego, PESEL 85010112345, e-mail jan@example.com, kwota 4 500,00 zł"
    scrubbed, count = scrub_text(text, {name_stem("Kowalski") or ""})
    assert count == 3
    assert "85010112345" not in scrubbed
    assert "jan@example.com" not in scrubbed
    assert "Kowalskiego" not in scrubbed
    assert "4 500,00 zł" in scrubbed


@pytest.mark.parametrize(
    ("text", "label", "value"),
    [
        ("Powód: Jan Sadowski", "IMIE/NAZWISKO", "Jan Sadowski"),
        ("Pozwany: Anna Bankiewicz", "IMIE/NAZWISKO", "Anna Bankiewicz"),
        ("Najemca: Tomasz Szkolnicki", "IMIE/NAZWISKO", "Tomasz Szkolnicki"),
        ("Pozwany: Jan Nipowski", "IMIE/NAZWISKO", "Jan Nipowski"),
        ("spotkanie z Panią Anną Nowak", "IMIE/NAZWISKO", "Anną Nowak"),
        ("pismo dla Pana Jana Nowaka", "IMIE/NAZWISKO", "Jana Nowaka"),
        ("Pełnomocnik: adw. Piotr Zieliński", "IMIE/NAZWISKO", "Piotr Zieliński"),
        ("UL. KWIATOWA 5", "ADRES", "UL. KWIATOWA 5"),
        ("Al. Jerozolimskie 12", "ADRES", "Al. Jerozolimskie 12"),
        ("ul. Lipowa 4, 20-001 Lublin", "ADRES", "ul. Lipowa 4, 20-001 Lublin"),
        ("mieszka w Zalesiu przy Leśnej 3", "ADRES", "Zalesiu przy Leśnej 3"),
        ("zamieszkała przy Leśnej 3", "ADRES", "Leśnej 3"),
        ("Słoneczna 8/2, Tychy", "ADRES", "Słoneczna 8/2, Tychy"),
        (
            "skrytka pocztowa nr 45, 00-950 Warszawa",
            "ADRES",
            "skrytka pocztowa nr 45, 00-950 Warszawa",
        ),
        ("Hauptstraße 5, 10115 Berlin", "ADRES", "Hauptstraße 5, 10115 Berlin"),
        ("Flat 3, 12 Rosebery Avenue", "ADRES", "Flat 3, 12 Rosebery Avenue"),
        ("dowodem osobistym serii ABC nr 123456", "NUMER DOKUMENTU", "ABC nr 123456"),
        ("samochodem Opel Astra KR 4A123", "NUMER REJESTRACYJNY", "KR 4A123"),
    ],
)
def test_cases_found_by_review(text: str, label: str, value: str) -> None:
    assert (label, value) in _found(text)


@pytest.mark.parametrize(
    "text",
    [
        "§ 2. Zleceniobiorca: zobowiązuje się do wykonania usługi",
        "Pozwany: Skarb Państwa reprezentowany przez Prezydenta Miasta",
        "Wykonawca: Budimex",
        "WITNESS STATEMENT",
        "Świadek Koronny",
        "zgodnie z pkt 3.1.12 regulaminu",
        "Section 3.1.12 applies",
        "Numer zamówienia 123456789",
        "Numer umowy: 20260100123",
        "kwota 150 000 000,00",
        "Address: as above",
    ],
)
def test_review_cases_that_must_stay_visible(text: str) -> None:
    assert _found(text) == []


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Nowak", "Nowakiem"),
        ("Marek", "Markiem"),
        ("Paweł", "Pawła"),
        ("Lewandowski", "Lewandowscy"),
    ],
)
def test_more_inflected_forms_share_a_stem(first: str, second: str) -> None:
    assert name_stem(first) == name_stem(second)


def test_office_titles_are_not_name_stems() -> None:
    assert name_stem("Starosta") is None
    assert name_stem("Wojewoda") is None


def test_email_regex_stays_fast_on_long_lines() -> None:
    import time

    for line in ("a." * 20000 + "x", "a. " * 20000 + "x", "a@" * 10000, "1 " * 20000):
        started = time.perf_counter()
        find_pattern_spans(line)
        assert time.perf_counter() - started < 2
