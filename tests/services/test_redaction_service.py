from app.enums.redaction import SensitiveDataType
from app.services.redaction import (
    DetectedSpan,
    RedactionBox,
    RedactionService,
    SensitiveDataDetector,
    WordBox,
    _valid_nip,
    _valid_pesel,
    _valid_regon,
)


def test_detector_detects_supported_regex_v1_types() -> None:
    text = (
        "PESEL 44051401359, NIP 123-456-32-18, REGON 12345678500002, "
        "IBAN PL61109010140000071219812874, email jan.kowalski@example.com, "
        "telefon +48 501 502 503, data 24.05.2026, kod 00-001, "
        "dowod ABC123456, paszport AB1234567"
    )

    spans = SensitiveDataDetector().detect(text)
    detected_types = {span.data_type for span in spans}

    assert {
        SensitiveDataType.PESEL,
        SensitiveDataType.NIP,
        SensitiveDataType.REGON,
        SensitiveDataType.IBAN,
        SensitiveDataType.EMAIL,
        SensitiveDataType.PHONE,
        SensitiveDataType.DATE,
        SensitiveDataType.POSTAL_CODE,
        SensitiveDataType.ID_NUMBER,
        SensitiveDataType.PASSPORT_NUMBER,
    }.issubset(detected_types)


def test_detector_detects_contextual_person_address_and_contact_data() -> None:
    text = (
        "Imie i nazwisko: Jan Kowalski, "
        "Adres: ul. Dluga 12/4, 00-001 Warszawa, "
        "Kontakt: sekretariat pok. 204"
    )

    spans = SensitiveDataDetector().detect(text)
    detected_types = {span.data_type for span in spans}

    assert SensitiveDataType.PERSON in detected_types
    assert SensitiveDataType.ADDRESS in detected_types
    assert SensitiveDataType.CONTACT in detected_types


def test_detector_skips_obvious_non_sensitive_numbers() -> None:
    text = "Numer zamowienia 12345, kwota 987, referencja AB12 i wersja 2026."

    spans = SensitiveDataDetector().detect(text)

    assert spans == []


def test_detector_regex_detects_common_person_and_company_names() -> None:
    spans = SensitiveDataDetector().detect("Strony umowy: Jan Kowalski oraz ACME Solutions LLC.")

    detected_types = {span.data_type for span in spans}

    assert SensitiveDataType.PERSON in detected_types
    assert SensitiveDataType.ORGANIZATION in detected_types


def test_pesel_checksum_accepts_valid() -> None:
    assert _valid_pesel("44051401359") is True


def test_pesel_checksum_rejects_invalid() -> None:
    assert _valid_pesel("44051401358") is False  # wrong check digit
    assert _valid_pesel("12345678901") is False


def test_nip_checksum_accepts_valid() -> None:
    # 123-456-32-18 → digits 1234563218; weights: 6,5,7,2,3,4,5,6,7
    # sum = 6+10+21+8+15+24+15+12+9 = 120; 120%11 = 10 → invalid? let me use known valid
    assert _valid_nip("526-021-50-88") is True


def test_nip_checksum_rejects_invalid() -> None:
    assert _valid_nip("123-456-78-90") is False


def test_regon9_checksum_accepts_valid() -> None:
    assert _valid_regon("123456785") is True


def test_regon9_checksum_rejects_invalid() -> None:
    assert _valid_regon("123456789") is False


def test_detector_rejects_pesel_with_bad_checksum() -> None:
    # 12345678901 - 11 digits but wrong checksum
    spans = SensitiveDataDetector().detect("PESEL: 12345678901")
    assert SensitiveDataType.PESEL not in {s.data_type for s in spans}


def test_detector_accepts_pesel_with_correct_checksum() -> None:
    spans = SensitiveDataDetector().detect("PESEL: 44051401359")
    assert SensitiveDataType.PESEL in {s.data_type for s in spans}


def test_build_text_with_offsets_and_spans_to_boxes_merges_line() -> None:
    """Words from the same span on the same line produce one wide box."""
    svc = RedactionService()
    words = [
        WordBox(text="Kontakt", left=0, top=0, width=40, height=10),
        WordBox(text="501", left=45, top=0, width=20, height=10),
        WordBox(text="502", left=70, top=0, width=20, height=10),
        WordBox(text="503", left=95, top=0, width=20, height=10),
    ]

    text, offsets = svc.build_text_with_offsets(words)
    span = DetectedSpan(
        start=text.index("501"),
        end=len(text),
        data_type=SensitiveDataType.PHONE,
    )

    boxes = svc.spans_to_boxes([span], offsets)

    assert text == "Kontakt 501 502 503"

    assert boxes == [RedactionBox((45, 0, 115, 10), SensitiveDataType.PHONE)]


def test_spans_to_boxes_produces_separate_boxes_for_different_lines() -> None:
    """Words from the same span but on different lines stay as separate boxes."""
    svc = RedactionService()
    words = [
        WordBox(text="Jan", left=0, top=0, width=30, height=12),
        WordBox(text="Kowalski", left=0, top=20, width=60, height=12),
    ]
    text, offsets = svc.build_text_with_offsets(words)
    span = DetectedSpan(start=0, end=len(text), data_type=SensitiveDataType.PERSON)

    boxes = svc.spans_to_boxes([span], offsets)

    assert len(boxes) == 2


def test_field_label_redacts_bounded_value_words() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="Adres:", left=10, top=20, width=45, height=12),
        WordBox(text="ul.", left=64, top=20, width=16, height=12),
        WordBox(text="Dluga", left=88, top=20, width=38, height=12),
        WordBox(text="12,", left=134, top=20, width=20, height=12),
        WordBox(text="00-001", left=162, top=20, width=42, height=12),
        WordBox(text="Warszawa", left=212, top=20, width=68, height=12),
    ]

    boxes = svc._field_line_boxes(words, (400, 200))

    assert boxes == [
        RedactionBox((64, 20, 280, 32), SensitiveDataType.ADDRESS),
    ]


def test_contact_field_labels_use_precise_types() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="Email:", left=10, top=20, width=40, height=12),
        WordBox(text="jan@example.com", left=58, top=20, width=110, height=12),
        WordBox(text="Telefon:", left=10, top=50, width=56, height=12),
        WordBox(text="+48", left=74, top=50, width=24, height=12),
    ]

    boxes = svc._field_line_boxes(words, (300, 120))

    assert boxes == [
        RedactionBox((58, 20, 168, 32), SensitiveDataType.EMAIL),
        RedactionBox((74, 50, 98, 62), SensitiveDataType.PHONE),
    ]


def test_contact_field_without_ocr_value_gets_bounded_fallback_box() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="Phone:", left=10, top=20, width=42, height=12),
    ]

    boxes = svc._field_line_boxes(words, (300, 120))

    assert boxes == [
        RedactionBox((66, 20, 300, 32), SensitiveDataType.PHONE),
    ]


def test_multilingual_field_labels_are_redacted() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="Full", left=10, top=20, width=24, height=12),
        WordBox(text="Name:", left=40, top=20, width=36, height=12),
        WordBox(text="Jane", left=84, top=20, width=28, height=12),
        WordBox(text="Smith", left=120, top=20, width=35, height=12),
        WordBox(text="Adresse:", left=10, top=50, width=54, height=12),
        WordBox(text="Rue", left=72, top=50, width=24, height=12),
        WordBox(text="Victor", left=104, top=50, width=38, height=12),
    ]

    boxes = svc._field_line_boxes(words, (300, 120))

    assert boxes == [
        RedactionBox((84, 20, 155, 32), SensitiveDataType.PERSON),
        RedactionBox((72, 50, 142, 62), SensitiveDataType.ADDRESS),
    ]


def test_city_label_without_separator_does_not_trigger_address_redaction() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="The", left=0, top=0, width=20, height=12),
        WordBox(text="city", left=25, top=0, width=24, height=12),
        WordBox(text="of", left=54, top=0, width=14, height=12),
        WordBox(text="Warsaw", left=72, top=0, width=50, height=12),
    ]

    assert svc._field_line_boxes(words, (300, 50)) == []


def test_city_label_with_colon_triggers_address_redaction() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="City:", left=0, top=0, width=30, height=12),
        WordBox(text="Warsaw", left=35, top=0, width=50, height=12),
    ]

    boxes = svc._field_line_boxes(words, (300, 50))

    assert boxes == [RedactionBox((35, 0, 85, 12), SensitiveDataType.ADDRESS)]


def test_city_context_pattern_requires_separator() -> None:
    detector = SensitiveDataDetector()

    without_separator = detector.detect("city Warsaw")
    assert SensitiveDataType.ADDRESS not in {s.data_type for s in without_separator}

    with_separator = detector.detect("city: Warsaw")
    assert SensitiveDataType.ADDRESS in {s.data_type for s in with_separator}


def test_detector_detects_longform_polish_date_with_diacritics() -> None:
    spans = SensitiveDataDetector().detect("Umowa zawarta dnia 28 stycznia 2019 r.")
    assert SensitiveDataType.DATE in {s.data_type for s in spans}


def test_detector_detects_longform_polish_date_ascii_fallback() -> None:
    # OCR sometimes strips diacritics: "wrzesnia" instead of "września"
    spans = SensitiveDataDetector().detect("Data: 3 wrzesnia 2020 r.")
    assert SensitiveDataType.DATE in {s.data_type for s in spans}


def test_detector_detects_longform_polish_date_without_suffix() -> None:
    spans = SensitiveDataDetector().detect("podpisano 15 marca 2021")
    assert SensitiveDataType.DATE in {s.data_type for s in spans}


def test_detector_detects_person_after_podpisal_label() -> None:
    # Surname only — not in the known first-names list
    spans = SensitiveDataDetector().detect("Podpisał: Kochanowska")
    assert SensitiveDataType.PERSON in {s.data_type for s in spans}


def test_detector_detects_person_after_signed_by_label() -> None:
    spans = SensitiveDataDetector().detect("Signed by: Nowak")
    assert SensitiveDataType.PERSON in {s.data_type for s in spans}


def test_date_field_label_redacts_following_tokens() -> None:
    svc = RedactionService()
    words = [
        WordBox(text="Data:", left=10, top=20, width=36, height=12),
        WordBox(text="28", left=54, top=20, width=16, height=12),
        WordBox(text="stycznia", left=76, top=20, width=52, height=12),
        WordBox(text="2019", left=134, top=20, width=30, height=12),
        WordBox(text="r.", left=170, top=20, width=12, height=12),
    ]

    boxes = svc._field_line_boxes(words, (400, 60))

    assert any(box.data_type == SensitiveDataType.DATE for box in boxes)
