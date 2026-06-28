from app.enums.document import DocumentType
from app.services.formal_validation import FormalDocumentExtractor, extract_text_from_ocr_raw


def test_extracts_contract_formal_fields_from_azure_content() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, 12.05.2026
        Strony umowy:
        Zamawiający: ABC sp. z o.o.
        Wykonawca: Jan Kowalski
        Przedmiot umowy:
        Wykonanie usługi informatycznej.
        Podpis Zamawiającego
        Załączniki:
        Załącznik nr 1
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.document_type == DocumentType.CONTRACT
    assert result.fields["has_date"] is True
    assert result.fields["has_parties_section"] is True
    assert result.fields["has_subject_section"] is True
    assert result.fields["has_signature"] is True
    assert result.fields["has_attachments_section"] is True


def test_treats_redacted_date_placeholder_as_present_date() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, [UTAJNIONO: DATA]
        Strony umowy:
        Zamawiający: ABC sp. z o.o.
        Wykonawca: Jan Kowalski
        Przedmiot umowy:
        Wykonanie usługi informatycznej.
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.document_type == DocumentType.CONTRACT
    assert result.fields["has_date"] is True
    assert result.fields["date_candidates"] == ["[UTAJNIONO: DATA]"]


def test_extracts_formal_fields_from_redacted_contract_text() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, [UTAJNIONO: DATA]
        1. Strony umowy
        Zamawiający: [UTAJNIONO: IMIE/NAZWISKO], [UTAJNIONO: ADRES]
        Wykonawca: [UTAJNIONO: IMIE/NAZWISKO], [UTAJNIONO: ADRES]
        2. Przedmiot umowy
        Przedmiotem umowy jest wykonanie usługi informatycznej.
        3. Załączniki
        Załącznik nr 1 - zakres prac.
        miejsce pozostawione puste
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.document_type == DocumentType.CONTRACT
    assert result.fields["has_date"] is True
    assert result.fields["has_parties_section"] is True
    assert result.fields["has_subject_section"] is True
    assert result.fields["has_attachments_section"] is True
    assert result.fields["has_signature"] is False
    assert result.fields["redaction_labels"] == ["adres", "data", "person"]


def test_detects_malformed_redacted_date_placeholder_from_ocr() -> None:
    raw = {
        "content": """
        UMOWA
        Warszawa, UTAJNIOM. DATA
        Strony umowy:
        Przedmiot umowy:
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_date"] is True
    assert result.fields["date_candidates"] == ["UTAJNIOM. DATA"]


def test_detects_ocr_spaced_numeric_date() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, 12 . 05 . 2026
        Strony umowy:
        Przedmiot umowy:
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_date"] is True
    assert result.fields["date_candidates"] == ["12 . 05 . 2026"]


def test_detects_iso_date_format() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Data zawarcia: 2026-05-12
        Strony umowy:
        Przedmiot umowy:
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_date"] is True
    assert result.fields["date_candidates"] == ["2026-05-12"]


def test_detects_sections_split_by_ocr_line_breaks() -> None:
    raw = {
        "content": """
        UMOWA
        Warszawa, 12.05.2026
        Strony
        umowy:
        Zamawiający: ABC sp. z o.o.
        Wykonawca: Jan Kowalski
        Przedmiot
        umowy:
        świadczenie usług informatycznych
        Podpis Zamawiającego
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_parties_section"] is True
    assert result.fields["has_subject_section"] is True
    assert result.fields["has_signature"] is True


def test_contract_title_does_not_count_as_subject_section() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, 2026-05-12
        1. Strony umowy
        Zamawiający: ABC sp. z o.o.
        Wykonawca: Jan Kowalski.
        3. Wynagrodzenie
        Strony ustalają wynagrodzenie ryczałtowe w wysokości 5000 PLN netto.
        Podpis Zamawiającego: ____________________
        Podpis Wykonawcy: ____________________
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_subject_section"] is False
    assert result.fields["has_attachments_section"] is False


def test_negated_section_terms_do_not_count_as_present() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, 2026-05-12
        1. Strony umowy
        Zamawiający: ABC sp. z o.o.
        Wykonawca: Jan Kowalski.
        Dokument testowy opisuje brak przedmiotu oraz brak załączników.
        Podpis Zamawiającego: ____________________
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_subject_section"] is False
    assert result.fields["has_attachments_section"] is False


def test_negated_attorney_term_does_not_count_as_attorney_section() -> None:
    raw = {
        "content": """
        PEŁNOMOCNICTWO
        Poznań, 12 . 05 . 2026
        Zakres pełnomocnictwa
        Upoważniam do reprezentowania mnie przed sądami i organami administracji.
        Dokument testowy opisuje brak mocodawcy i pełnomocnika.
        Podpis mocodawcy: ____________________
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_principal_section"] is False
    assert result.fields["has_attorney_section"] is False
    assert result.fields["has_authorization_scope_section"] is True


def test_negative_signature_phrase_does_not_count_as_signature() -> None:
    raw = {
        "content": """
        UMOWA O ŚWIADCZENIE USŁUG
        Warszawa, 12.05.2026
        Strony umowy:
        Zamawiający: ABC sp. z o.o.
        Wykonawca: Jan Kowalski
        Przedmiot umowy:
        Dokument testowy zawiera brak podpisu.
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.fields["has_signature"] is False


def test_extracts_lawsuit_missing_signature() -> None:
    raw = {
        "content": """
        Pozew o zapłatę
        Kraków, 1 czerwca 2026
        Sąd Rejonowy dla Krakowa-Śródmieścia
        Powód: Anna Nowak
        Pozwany: ABC sp. z o.o.
        Wnoszę o zasądzenie kwoty 5000 PLN.
        Wartość przedmiotu sporu: 5000 PLN.
        Uzasadnienie: pozwany nie zapłacił za wykonaną usługę.
        Załączniki:
        faktura VAT
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.document_type == DocumentType.LAWSUIT
    assert result.fields["has_date"] is True
    assert result.fields["has_court_section"] is True
    assert result.fields["has_parties_section"] is True
    assert result.fields["has_claim_section"] is True
    assert result.fields["has_justification_section"] is True
    assert result.fields["has_attachments_section"] is True
    assert result.fields["has_signature"] is False


def test_extracts_power_of_attorney_formal_fields() -> None:
    raw = {
        "content": """
        Pełnomocnictwo
        Gdańsk, 7.06.2026
        Mocodawca: Jan Kowalski
        Pełnomocnik: Anna Nowak
        Udzielam pełnomocnictwa do reprezentowania mnie przed sądami.
        Zakres pełnomocnictwa obejmuje składanie pism i odbiór korespondencji.
        Podpis mocodawcy
        """
    }

    result = FormalDocumentExtractor().extract(raw)

    assert result.document_type == DocumentType.POWER_OF_ATTORNEY
    assert result.fields["has_date"] is True
    assert result.fields["has_principal_section"] is True
    assert result.fields["has_attorney_section"] is True
    assert result.fields["has_authorization_scope_section"] is True
    assert result.fields["has_signature"] is True


def test_extract_text_from_local_ocr_words() -> None:
    raw = {
        "words_per_page": [
            [{"tekst": "Pozew"}, {"tekst": "o"}, {"tekst": "zapłatę"}],
            [{"tekst": "Powód:"}, {"tekst": "Anna"}, {"tekst": "Nowak"}],
        ]
    }

    assert extract_text_from_ocr_raw(raw) == "Pozew o zapłatę\nPowód: Anna Nowak"
