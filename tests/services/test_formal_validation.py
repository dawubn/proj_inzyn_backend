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
