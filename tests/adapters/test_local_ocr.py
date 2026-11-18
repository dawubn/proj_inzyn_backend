"""Geometry of local OCR results: rotated pages, PDF text layer and glued captions."""

import fitz
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.adapters.local_ocr import (
    PageRead,
    _pdf_text_words,
    _rotate_words,
    _rotate_words_back,
    _split_glued,
)


def _word(x: int, y: int, w: int, h: int, text: str = "x") -> dict[str, object]:
    return {"tekst": text, "x": x, "y": y, "szerokosc": w, "wysokosc": h, "linia": "1"}


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotation_round_trip_keeps_boxes(rotation: int) -> None:
    size = (400, 600)
    word = _word(30, 50, 120, 20)
    upright_size = size if rotation == 180 else (size[1], size[0])
    upright = _rotate_words([word], rotation, size)
    assert _rotate_words_back(upright, rotation, upright_size)[0] == word


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_boxes_from_upright_copy_land_on_the_supplied_page(rotation: int) -> None:
    page = Image.new("L", (400, 600), 255)
    ImageDraw.Draw(page).rectangle([30, 50, 150, 70], fill=0)
    upright = page.rotate(-rotation, expand=True)
    ys, xs = np.nonzero(np.asarray(upright) < 128)
    box = _word(int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min()))
    read = PageRead(image=upright, rotation=rotation)

    restored = read.restore_boxes([box])[0]

    assert abs(restored["x"] - 30) <= 2
    assert abs(restored["y"] - 50) <= 2
    assert read.restore_image(upright).size == page.size


def test_pdf_text_layer_follows_page_rotation() -> None:
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 60), "PESEL", fontsize=20)
    page.set_rotation(90)
    zoom = 2.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n)
    ys, xs = np.nonzero(image[:, :, 0] < 128)

    word = _pdf_text_words(page, zoom)[0]

    assert word["x"] <= xs.min() + 4
    assert word["y"] <= ys.min() + 4
    assert word["x"] + word["szerokosc"] >= xs.max() - 4
    assert word["y"] + word["wysokosc"] >= ys.max() - 4


def test_glued_caption_and_value_are_split() -> None:
    caption, value = _split_glued(_word(100, 0, 340, 30, "PESEL:92031504567"))
    assert caption["tekst"] == "PESEL:"
    assert value["tekst"] == "92031504567"
    assert value["x"] < caption["x"] + caption["szerokosc"]
    assert value["x"] + value["szerokosc"] == 440
    assert _split_glued(_word(0, 0, 50, 10, "12:30")) == [_word(0, 0, 50, 10, "12:30")]
