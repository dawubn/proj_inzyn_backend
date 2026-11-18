"""Mask geometry, labels and signature detection, without OCR engines."""

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services.redaction_service import _blank_masked, _draw, _label_layout, _Mask, _merge
from app.services.redaction_visual import find_visual_regions

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _box(mask: _Mask) -> tuple[int, int, int, int]:
    return mask.x0, mask.y0, mask.x1, mask.y1


def test_words_of_one_value_become_one_mask() -> None:
    masks = _merge(
        [
            _Mask(100, 10, 160, 40, "[UTAJNIONO: IMIE/NAZWISKO]"),
            _Mask(170, 10, 260, 40, "[UTAJNIONO: IMIE/NAZWISKO]"),
            _Mask(400, 10, 500, 40, "[UTAJNIONO: PESEL]"),
        ]
    )
    assert sorted(_box(m) for m in masks) == [(100, 10, 260, 40), (400, 10, 500, 40)]


def test_masks_of_neighbouring_lines_are_not_joined() -> None:
    label = "[UTAJNIONO: ADRES]"
    masks = _merge([_Mask(100, 10, 300, 40, label), _Mask(100, 60, 300, 90, label)])
    assert len(masks) == 2


def test_same_value_from_two_engines_is_one_mask() -> None:
    masks = _merge(
        [
            _Mask(100, 10, 300, 40, "[UTAJNIONO: PESEL]"),
            _Mask(98, 8, 302, 42, "[UTAJNIONO: TELEFON]"),
        ]
    )
    assert len(masks) == 1


def test_second_reading_keeps_values_that_are_only_partly_masked() -> None:
    masks = [_Mask(100, 0, 200, 40, "[UTAJNIONO: PESEL]")]
    words = [
        {"tekst": "UTAJNIONO:", "x": 105, "y": 5, "szerokosc": 80, "wysokosc": 30},
        {"tekst": "85010112345", "x": 180, "y": 5, "szerokosc": 200, "wysokosc": 30},
    ]
    blanked = _blank_masked(words, masks)
    assert blanked[0]["tekst"] == "[UTAJNIONO]"
    assert blanked[1]["tekst"] == "85010112345"


def test_label_fits_or_is_split_over_two_lines() -> None:
    wide = _label_layout("[UTAJNIONO: IMIE/NAZWISKO]", 700, 60)
    narrow = _label_layout("[UTAJNIONO: NUMER DOKUMENTU]", 260, 70)
    assert wide is not None
    assert len(wide[0]) == 1
    assert narrow is not None
    assert narrow[0] == ["[UTAJNIONO:", "NUMER DOKUMENTU]"]
    assert _label_layout("[UTAJNIONO: ADRES]", 20, 10) is None


def test_label_is_readable_on_the_mask() -> None:
    image = Image.new("RGB", (800, 120), "white")
    masked = _draw(image, [_Mask(50, 20, 750, 100, "[UTAJNIONO: PESEL]")])
    region = np.asarray(masked)[20:100, 50:750]
    white = (region.min(axis=2) > 200).mean()
    assert 0.03 < white < 0.5


def test_signature_shape_masks_strokes_and_not_the_text_above() -> None:
    image = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 30, 400, 62], fill=(20, 20, 20))
    shape = np.zeros((120, 300), dtype=bool)
    shape[40:80, 20:280] = True
    masked = _draw(image, [_Mask(300, 60, 600, 180, "[UTAJNIONO: PODPIS]", shape)])
    pixels = np.asarray(masked)
    assert pixels[45, 100].max() < 60  # printed line above stays as it was
    assert pixels[120, 450].max() < 60  # stroke area is black
    assert pixels[70, 590].min() > 200  # inside the box but away from the strokes


@pytest.mark.skipif(not FONT.exists(), reason="DejaVu font is not installed")
def test_signature_after_caption_is_found() -> None:
    image = Image.new("RGB", (1400, 400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 38)
    draw.text((60, 200), "Podpis mocodawcy:", fill=(20, 20, 20), font=font)
    for start in range(0, 360, 40):
        draw.arc([480 + start, 150, 560 + start, 270], 0, 300, fill=(30, 60, 170), width=5)
    draw.line([470, 270, 860, 160], fill=(30, 60, 170), width=5)
    words = [
        {
            "tekst": "Podpis",
            "x": 60,
            "y": 205,
            "szerokosc": 120,
            "wysokosc": 38,
            "linia": "1",
            "zrodlo": "tesseract",
        },
        {
            "tekst": "mocodawcy:",
            "x": 195,
            "y": 205,
            "szerokosc": 210,
            "wysokosc": 38,
            "linia": "1",
            "zrodlo": "tesseract",
        },
        {
            "tekst": "Podpis",
            "x": 61,
            "y": 204,
            "szerokosc": 121,
            "wysokosc": 40,
            "linia": "r1",
            "zrodlo": "rapidocr",
        },
        {
            "tekst": "mocodawcy:",
            "x": 196,
            "y": 204,
            "szerokosc": 211,
            "wysokosc": 40,
            "linia": "r1",
            "zrodlo": "rapidocr",
        },
    ]

    regions = find_visual_regions(image, words)

    signatures = [r for r in regions if r["rodzaj_danych"] == "[UTAJNIONO: PODPIS]"]
    assert len(signatures) == 1
    assert signatures[0]["x"] <= 480
    assert signatures[0]["x"] + signatures[0]["szerokosc"] >= 860
    assert signatures[0]["piksele"].any()


@pytest.mark.skipif(not FONT.exists(), reason="DejaVu font is not installed")
def test_dotted_signature_line_is_not_a_signature() -> None:
    image = Image.new("RGB", (1400, 300), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 38)
    draw.text((60, 120), "Podpis: ........................", fill=(20, 20, 20), font=font)
    words = [
        {
            "tekst": "Podpis:",
            "x": 60,
            "y": 125,
            "szerokosc": 140,
            "wysokosc": 38,
            "linia": "1",
            "zrodlo": "tesseract",
        },
        {
            "tekst": "Podpis:",
            "x": 60,
            "y": 124,
            "szerokosc": 141,
            "wysokosc": 40,
            "linia": "r1",
            "zrodlo": "rapidocr",
        },
    ]
    assert find_visual_regions(image, words) == []


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_end_to_end_rotated_page_is_masked(tmp_path: Path) -> None:
    from app.services.redaction_service import RedactionService

    image = Image.new("RGB", (2480, 3508), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 40)
    lines = ["PEŁNOMOCNICTWO", "Powód: Jan Kowalski", "PESEL: 85010112345", "Kwota: 4 500,00 zł"]
    for index, line in enumerate(lines):
        draw.text((200, 300 + index * 100), line, fill=(20, 20, 20), font=font)
    rotated = image.rotate(90, expand=True)
    path = tmp_path / "page.png"
    rotated.save(path)

    result = RedactionService().anonymize_file(path, "page.png", "image/png")

    output = Image.open(result.output_path).convert("RGB")
    assert output.size == rotated.size
    labels = {r["rodzaj_danych"] for r in result.regions[0]}
    assert {"[UTAJNIONO: IMIE/NAZWISKO]", "[UTAJNIONO: PESEL]"} <= labels
    assert "[UTAJNIONO: ADRES]" not in labels
