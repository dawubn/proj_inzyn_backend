"""
Coverage check for anonymization on synthetic PL and EN documents, run in the worker container.

    python scripts/evaluate_redaction.py --variants scan,photo --write-fixtures tests/fixtures/redaction
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("APP_SECRET_KEY", "evaluation")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.com")
os.environ.setdefault("AZURE_DOCUMENT_INTELLIGENCE_KEY", "x")

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.adapters.local_ocr import LocalOCRAdapter
from app.services.redaction_service import RedactionService

PAGE_WIDTH = 2480
PAGE_HEIGHT = 3508
DPI = 300
MARGIN = 180
LINE_HEIGHT = 92
BODY_SIZE = 38
INK = (20, 20, 20)
PEN = (30, 60, 170)

_FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/"
_FONT_FILES = {
    "sans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "hand": _FONT_URL + "ofl/caveat/Caveat%5Bwght%5D.ttf",
    "hand_en": _FONT_URL + "apache/homemadeapple/HomemadeApple-Regular.ttf",
    "signature": _FONT_URL + "ofl/mrssaintdelafield/MrsSaintDelafield-Regular.ttf",
}
_FONT_SIZES = {"sans": BODY_SIZE, "hand": 58, "hand_en": 42, "signature": 110}

KEEP = "keep"
VISUAL_LABELS = {"PODPIS"}


@dataclass(frozen=True)
class Seg:
    """Fragment of a line; kind is KEEP, a mask label or None for neutral text."""

    text: str
    kind: str | None = None
    font: str = "sans"
    ner: bool = False


def k(text: str) -> Seg:
    return Seg(text, KEEP)


def m(text: str, label: str, *, font: str = "sans", ner: bool = False) -> Seg:
    return Seg(text, label, font, ner)


def n(text: str) -> Seg:
    return Seg(text)


@dataclass(frozen=True)
class CorpusDocument:
    name: str
    language: str
    lines: list[list[Seg]]


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    seg: Seg
    baseline: float = 0.0

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)


@dataclass
class WordBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class Rendered:
    image: Image.Image
    boxes: list[Box]
    pdf: bytes | None = None
    content_type: str = "image/png"
    keep_words: list[WordBox] = field(default_factory=list)


@dataclass
class Score:
    document: str
    variant: str
    sensitive: int = 0
    leaked: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    coverage: float = 0.0
    extra_area: float = 0.0
    seconds: float = 0.0


IMIE = "IMIE/NAZWISKO"

CORPUS: list[CorpusDocument] = [
    CorpusDocument(
        "pl_pelnomocnictwo",
        "pl",
        [
            [k("Testowo, "), k("12 maja 2026 r.")],
            [k("PEŁNOMOCNICTWO")],
            [k("Ja, niżej podpisana "), m("Anna Przykładowa", IMIE)],
            [k("zamieszkała przy "), m("ul. Przykładowej 8 / 2, 00-000 Testowo", "ADRES")],
            [k("legitymująca się dowodem osobistym "), m("TST 000000", "NUMER DOKUMENTU")],
            [k("PESEL "), m("85010112345", "PESEL")],
            [k("Ustanawiam pełnomocnikiem "), m("Jakuba Przykładowego", IMIE, ner=True)],
            [k("zamieszkałego przy "), m("al. Testowej 15/3, 00-950 Testowo", "ADRES")],
            [
                k("adres e-mail: "),
                m("anna@example.invalid", "EMAIL"),
                k(", tel. "),
                m("+48 601 234 567", "TELEFON"),
            ],
            [k("do reprezentowania mnie przed Wojewódzkim Funduszem Ochrony Środowiska")],
            [k("z siedzibą w Testowie w sprawie programu „Czyste Powietrze”.")],
            [k("Korespondencję należy kierować do "), n("Pani "), m("Przykładowej", IMIE), n(".")],
            [k("Pełnomocnictwo jest ważne do dnia "), k("31.12.2026"), n(".")],
            [k("Podpis mocodawcy: "), m("A. Przykładowa", "PODPIS", font="signature")],
        ],
    ),
    CorpusDocument(
        "pl_pozew",
        "pl",
        [
            [k("Testowo, "), k("15.05.2026 r.")],
            [k("Sąd Rejonowy w Testowie")],
            [k("I Wydział Cywilny")],
            [n("ul. Sądowa 1, 00-001 Testowo")],
            [k("Powód: "), m("JAN KOWALSKI", IMIE)],
            [k("zam. "), m("ul. Kwiatowa 15/3, 02-495 Warszawa", "ADRES")],
            [k("PESEL: "), m("850101 12345", "PESEL")],
            [k("Pozwana: "), m("Anna Nowak-Wiśniewska", IMIE)],
            [k("zam. "), m("os. Słoneczne 4 m. 12, 61-123 Poznań", "ADRES")],
            [k("POZEW O ZAPŁATĘ")],
            [k("Wartość przedmiotu sporu: 4 500,00 zł")],
            [k("Wnoszę o zasądzenie od pozwanej "), m("Anny Nowak-Wiśniewskiej", IMIE)],
            [k("kwoty 4 500,00 zł wraz z odsetkami od dnia "), k("1 marca 2026 r.")],
            [k("na rachunek bankowy nr "), m("61 1090 1014 0000 0712 1981 2874", "NUMER RACHUNKU")],
            [
                k("Kontakt: "),
                m("jan.kowalski@example.com", "EMAIL"),
                k(", tel. "),
                m("601-234-567", "TELEFON"),
            ],
            [k("Sygnatura akt: I C 123/26")],
            [k("Podpis powoda: "), m("Jan Kowalski", "PODPIS", font="signature")],
        ],
    ),
    CorpusDocument(
        "pl_umowa_najmu",
        "pl",
        [
            [k("UMOWA NAJMU LOKALU MIESZKALNEGO")],
            [k("zawarta w dniu "), k("12.03.2026"), k(" w Poznaniu pomiędzy:")],
            [
                m("Janem Kowalskim", IMIE, ner=True),
                k(", PESEL "),
                m("85010112345", "PESEL"),
                k(", zwanym dalej Wynajmującym,"),
            ],
            [
                k("a "),
                m("Marią Zielińską", IMIE, ner=True),
                k(", NIP "),
                m("526-104-08-28", "NIP"),
                k(", REGON "),
                m("012345678", "REGON"),
                n(","),
            ],
            [k("zwaną dalej Najemcą.")],
            [k("§ 1. Przedmiotem umowy jest lokal przy "), m("ul. Polnej 5 m. 7", "ADRES")],
            [m("60-001 Poznań", "ADRES"), k(", dla którego prowadzona jest księga wieczysta")],
            [m("PO1P/00012345/6", "NUMER KW"), n(".")],
            [k("§ 2. Czynsz wynosi 2 500,00 zł miesięcznie, płatny na rachunek")],
            [m("PL 61 1090 1014 0000 0712 1981 2874", "NUMER RACHUNKU"), n(".")],
            [
                k("§ 3. Wynajmujący oświadcza, że "),
                m("Kowalski", IMIE, ner=True),
                k(" jest właścicielem lokalu."),
            ],
            [
                k("§ 4. Najemca parkuje pojazd o numerze rejestracyjnym "),
                m("PO 12345", "NUMER REJESTRACYJNY"),
            ],
            [k("Administrator: Testowa Firma Sp. z o.o., KRS "), m("0000123456", "KRS")],
            [n("Wynajmujący                                   Najemca")],
            [
                m("J. Kowalski", "PODPIS", font="signature"),
                n("                    "),
                m("M. Zielinska", "PODPIS", font="signature"),
            ],
        ],
    ),
    CorpusDocument(
        "pl_faktura",
        "pl",
        [
            [k("FAKTURA VAT nr FV/123/2026")],
            [k("Data wystawienia: "), k("01.10.2026")],
            [k("Sprzedawca: Testowa Firma Alfa Sp. z o.o.")],
            [n("ul. Fabryczna 10, 00-002 Testowo")],
            [k("NIP: "), m("1234563218", "NIP")],
            [k("Nabywca: "), m("Katarzyna Wójcik", IMIE)],
            [m("ul. Lipowa 7 m. 12, 61-123 Poznań", "ADRES")],
            [k("Usługa doradcza 1 szt. 1 000,00 zł netto, VAT 23%")],
            [k("Razem do zapłaty: 1 230,00 zł")],
            [k("Termin płatności: 14 dni, przelew na rachunek:")],
            [m("61 1090 1014 0000 0712 1981 2874", "NUMER RACHUNKU")],
        ],
    ),
    CorpusDocument(
        "pl_formularz_odreczny",
        "pl",
        [
            [k("WNIOSEK O WYDANIE ZAŚWIADCZENIA")],
            [k("Imię i nazwisko: "), m("Katarzyna Wójcik", IMIE, font="hand")],
            [k("PESEL: "), m("92031504567", "PESEL", font="hand")],
            [
                k("Adres zamieszkania: "),
                m("ul. Lipowa 7 m. 12, 61-123 Poznań", "ADRES", font="hand"),
            ],
            [k("Telefon: "), m("512 345 678", "TELEFON", font="hand")],
            [k("Seria i numer dowodu: "), m("ABC 123456", "NUMER DOKUMENTU", font="hand")],
            [k("Oświadczam, że dane podane we wniosku są zgodne z prawdą.")],
            [
                k("Data: "),
                Seg("14.05.2026", KEEP, "hand"),
                k("        Podpis: "),
                m("K. Wójcik", "PODPIS", font="signature"),
            ],
        ],
    ),
    CorpusDocument(
        "en_lease",
        "en",
        [
            [k("RESIDENTIAL LEASE AGREEMENT")],
            [k("This agreement is made on "), k("March 12, 2026"), k(" between:")],
            [k("Landlord: "), m("John Smith", IMIE)],
            [k("Address: "), m("221B Baker Street, London NW1 6XE", "ADRES")],
            [k("Tenant: "), m("Mary O'Connor-Lee", IMIE)],
            [k("Date of birth: "), k("4 July 1990")],
            [
                k("Phone: "),
                m("+44 20 7946 0958", "TELEFON"),
                k("  Email: "),
                m("mary.oconnor@example.org", "EMAIL"),
            ],
            [k("Passport No. "), m("123456789", "NUMER DOKUMENTU")],
            [k("National Insurance number: "), m("QQ 12 34 56 C", "NUMER IDENTYFIKACYJNY")],
            [k("Monthly rent: GBP 1,500.00 payable to IBAN")],
            [m("GB82 WEST 1234 5698 7654 32", "NUMER RACHUNKU")],
            [k("Deposit paid by card "), m("4111 1111 1111 1111", "NUMER KARTY")],
            [
                k("The Tenant, "),
                n("Ms "),
                m("O'Connor-Lee", IMIE),
                k(", agrees to keep the property in good repair."),
            ],
            [k("Signed by: "), m("John Smith", "PODPIS", font="signature")],
        ],
    ),
    CorpusDocument(
        "en_claim",
        "en",
        [
            [k("IN THE COUNTY COURT AT MANCHESTER")],
            [k("Claim No. K12AB345")],
            [k("Claimant: "), m("Robert Brown", IMIE)],
            [k("of "), m("742 Evergreen Terrace, Springfield, IL 62704", "ADRES")],
            [k("SSN: "), m("078-05-1120", "NUMER IDENTYFIKACYJNY")],
            [k("Defendant: ACME Trading Ltd, company number "), n("01234567")],
            [k("Amount claimed: GBP 4,500.00")],
            [
                k("Particulars of claim: on "),
                k("1st of February 2026"),
                k(" the Claimant delivered goods."),
            ],
            [
                n("Mr "),
                m("Brown", IMIE),
                k(" can be contacted at "),
                m("robert.brown@example.net", "EMAIL"),
            ],
            [k("or by phone "), m("+1 (555) 123-4567", "TELEFON"), n(".")],
            [k("Statement of truth. Signed: "), m("R. Brown", "PODPIS", font="hand_en")],
        ],
    ),
]


def _font_file(name: str) -> str:
    source = _FONT_FILES[name]
    if not source.startswith("https://"):
        return source
    cache = Path(tempfile.gettempdir()) / "cerberdoc_eval_fonts"
    cache.mkdir(exist_ok=True)
    target = cache / Path(source).name.replace("%5B", "[").replace("%5D", "]")
    if not target.exists():
        with urllib.request.urlopen(source, timeout=30) as response:  # noqa: S310
            target.write_bytes(response.read())
    return str(target)


def _font(name: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_file(name), _FONT_SIZES[name])


def _ink(seg: Seg) -> tuple[int, int, int]:
    return INK if seg.font == "sans" else PEN


def render(document: CorpusDocument) -> Rendered:
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    boxes: list[Box] = []
    keep_words: list[WordBox] = []
    baseline = MARGIN + LINE_HEIGHT
    for line in document.lines:
        x = float(MARGIN)
        for seg in line:
            font = _font(seg.font)
            lead = seg.text[: len(seg.text) - len(seg.text.lstrip())]
            text = seg.text.strip()
            start = x + draw.textlength(lead, font=font)
            if text:
                draw.text((start, baseline), text, fill=_ink(seg), font=font, anchor="ls")
                x0, y0, x1, y1 = draw.textbbox((start, baseline), text, font=font, anchor="ls")
                boxes.append(Box(x0, y0, x1, y1, seg, baseline))
                if seg.kind == KEEP:
                    keep_words.extend(_word_boxes(draw, text, start, baseline, font))
            x += draw.textlength(seg.text, font=font)
        if x > PAGE_WIDTH - MARGIN:
            raise ValueError(f"Line too long in {document.name}: {line}")
        baseline += LINE_HEIGHT
    return Rendered(image, boxes, keep_words=keep_words)


def _word_boxes(
    draw: ImageDraw.ImageDraw,
    text: str,
    start: float,
    baseline: float,
    font: ImageFont.FreeTypeFont,
) -> list[WordBox]:
    result = []
    position = 0
    for word in text.split(" "):
        if any(c.isalnum() for c in word):
            x = start + draw.textlength(text[:position], font=font)
            x0, y0, x1, y1 = draw.textbbox((x, baseline), word, font=font, anchor="ls")
            result.append(WordBox(x0, y0, x1, y1, word))
        position += len(word) + 1
    return result


def render_pdf(document: CorpusDocument) -> Rendered:
    """Digital PDF: printed text as a text layer, handwriting and signatures as images."""
    base = render(document)
    scale = 72 / DPI
    pdf = fitz.open()
    page = pdf.new_page(width=PAGE_WIDTH * scale, height=PAGE_HEIGHT * scale)
    sans = _font_file("sans")
    page.insert_font(fontname="dejavu", fontfile=sans)
    for box in base.boxes:
        rect = fitz.Rect(box.x0 * scale, box.y0 * scale, box.x1 * scale, box.y1 * scale)
        if box.seg.font == "sans":
            page.insert_text(
                (rect.x0, box.baseline * scale),
                box.seg.text.strip(),
                fontname="dejavu",
                fontsize=BODY_SIZE * scale,
            )
        else:
            crop = base.image.crop((int(box.x0), int(box.y0), int(box.x1) + 1, int(box.y1) + 1))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            page.insert_image(rect, stream=buf.getvalue())
    data = pdf.tobytes()
    pdf.close()
    return Rendered(
        base.image, base.boxes, pdf=data, content_type="application/pdf", keep_words=base.keep_words
    )


def _rotate_boxes(boxes: list[Box], size: tuple[int, int], angle: float, expand: bool) -> list[Box]:
    width, height = size
    rad = math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    if expand:
        new_w = abs(width * cos) + abs(height * sin)
        new_h = abs(width * sin) + abs(height * cos)
    else:
        new_w, new_h = width, height
    cx, cy, ncx, ncy = width / 2, height / 2, new_w / 2, new_h / 2

    def turn(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - cx, y - cy
        return ncx + dx * cos + dy * sin, ncy - dx * sin + dy * cos

    rotated = []
    for box in boxes:
        corners = [turn(x, y) for x in (box.x0, box.x1) for y in (box.y0, box.y1)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        rotated.append(Box(min(xs), min(ys), max(xs), max(ys), box.seg))
    return rotated


def _rotated(base: Rendered, angle: float, expand: bool) -> Rendered:
    image = base.image.rotate(angle, expand=expand, fillcolor="white")
    boxes = _rotate_boxes(base.boxes, base.image.size, angle, expand)
    words = _rotate_boxes(
        [Box(w.x0, w.y0, w.x1, w.y1, k(w.text)) for w in base.keep_words],
        base.image.size,
        angle,
        expand,
    )
    keep_words = [WordBox(b.x0, b.y0, b.x1, b.y1, b.seg.text) for b in words]
    return Rendered(image, boxes, keep_words=keep_words)


def _photo(base: Rendered) -> Rendered:
    tilted = _rotated(base, 1.5, expand=False)
    image = tilted.image.filter(ImageFilter.GaussianBlur(1.1))
    image = ImageEnhance.Contrast(image).enhance(0.75)
    image = ImageEnhance.Brightness(image).enhance(0.92)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=60)
    return Rendered(
        Image.open(io.BytesIO(buf.getvalue())).convert("RGB"),
        tilted.boxes,
        keep_words=tilted.keep_words,
    )


def _low_resolution(base: Rendered) -> Rendered:
    small = base.image.resize((PAGE_WIDTH // 3, PAGE_HEIGHT // 3), Image.Resampling.BILINEAR)
    factor = 1 / 3
    boxes = [
        Box(b.x0 * factor, b.y0 * factor, b.x1 * factor, b.y1 * factor, b.seg) for b in base.boxes
    ]
    keep_words = [
        WordBox(w.x0 * factor, w.y0 * factor, w.x1 * factor, w.y1 * factor, w.text)
        for w in base.keep_words
    ]
    return Rendered(small, boxes, keep_words=keep_words)


VARIANTS: dict[str, Callable[[CorpusDocument], Rendered]] = {
    "scan": render,
    "photo": lambda d: _photo(render(d)),
    "rot90": lambda d: _rotated(render(d), 90, expand=True),
    "rot180": lambda d: _rotated(render(d), 180, expand=True),
    "lowres": lambda d: _low_resolution(render(d)),
    "pdf": render_pdf,
}


def _input_file(rendered: Rendered, directory: Path, stem: str) -> Path:
    if rendered.pdf is not None:
        path = directory / f"{stem}.pdf"
        path.write_bytes(rendered.pdf)
    else:
        path = directory / f"{stem}.png"
        rendered.image.save(path)
    return path


def _overlap(a: Box, x0: float, y0: float, x1: float, y1: float) -> float:
    w = min(a.x1, x1) - max(a.x0, x0)
    h = min(a.y1, y1) - max(a.y0, y0)
    return max(0.0, w) * max(0.0, h)


def _covered(box: Box, regions: list[dict[str, Any]]) -> float:
    """Exact share of the box area covered by the union of mask rectangles."""
    if box.area <= 0:
        return 1.0
    xs = sorted(
        {box.x0, box.x1}
        | {min(max(r["x"], box.x0), box.x1) for r in regions}
        | {min(max(r["x"] + r["szerokosc"], box.x0), box.x1) for r in regions}
    )
    covered = 0.0
    for left, right in pairwise(xs):
        spans = sorted(
            (max(r["y"], box.y0), min(r["y"] + r["wysokosc"], box.y1))
            for r in regions
            if r["x"] <= left and r["x"] + r["szerokosc"] >= right
        )
        top, length = box.y0, 0.0
        for start, end in spans:
            visible = max(start, top)
            if end > visible:
                length += end - visible
                top = end
        covered += (right - left) * length
    return covered / box.area


def _source_pixels(rendered: Rendered) -> np.ndarray:
    """The page as the pipeline sees it before masking (a digital PDF is rendered first)."""
    if rendered.pdf is None:
        return np.asarray(rendered.image, dtype=np.int16)
    with fitz.open(stream=rendered.pdf, filetype="pdf") as doc:
        pixmap = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72))
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return np.asarray(image, dtype=np.int16)


def _region_raster(regions: list[dict[str, Any]], size: tuple[int, int]) -> np.ndarray:
    raster = np.zeros((size[1], size[0]), dtype=bool)
    for r in regions:
        x0, y0 = max(0, int(r["x"])), max(0, int(r["y"]))
        x1 = min(size[0], int(r["x"] + r["szerokosc"]))
        y1 = min(size[1], int(r["y"] + r["wysokosc"]))
        if x1 <= x0 or y1 <= y0:
            continue
        shape = r.get("piksele")
        if shape is None:
            raster[y0:y1, x0:x1] = True
        else:
            resized = Image.fromarray(shape.astype(np.uint8) * 255).resize((x1 - x0, y1 - y0))
            raster[y0:y1, x0:x1] |= np.asarray(resized) > 0
    return raster


def _ink_hidden(
    source: np.ndarray, output: np.ndarray, masked: np.ndarray
) -> Callable[[float, float, float, float], float]:
    """Share of the ink in a box that is masked or no longer looks like the original."""
    ink = source.min(axis=2) < 200
    blacked = (output.max(axis=2) == 0) & (source.max(axis=2) > 0)
    changed = masked | (np.abs(output - source).max(axis=2) > 40) | blacked

    def share(x0: float, y0: float, x1: float, y1: float) -> float:
        rows = slice(max(0, int(y0)), max(0, int(y1) + 1))
        cols = slice(max(0, int(x0)), max(0, int(x1) + 1))
        box_ink = ink[rows, cols]
        if not box_ink.any():
            return 1.0
        return float((changed[rows, cols] & box_ink).sum() / box_ink.sum())

    return share


def _point_masked(x: float, y: float, regions: list[dict[str, Any]]) -> bool:
    for r in regions:
        if not (r["x"] <= x <= r["x"] + r["szerokosc"] and r["y"] <= y <= r["y"] + r["wysokosc"]):
            continue
        shape = r.get("piksele")
        if shape is None:
            return True
        row = min(shape.shape[0] - 1, int((y - r["y"]) / r["wysokosc"] * shape.shape[0]))
        col = min(shape.shape[1] - 1, int((x - r["x"]) / r["szerokosc"] * shape.shape[1]))
        if shape[row, col]:
            return True
    return False


def _scale_regions(
    regions: list[dict[str, Any]], source: tuple[int, int], target: tuple[int, int]
) -> list[dict[str, Any]]:
    fx, fy = target[0] / source[0], target[1] / source[1]
    return [
        {
            "x": r["x"] * fx,
            "y": r["y"] * fy,
            "szerokosc": r["szerokosc"] * fx,
            "wysokosc": r["wysokosc"] * fy,
            "piksele": r.get("piksele"),
        }
        for r in regions
    ]


def evaluate(document: CorpusDocument, variant: str, keep_dir: Path | None) -> Score:
    rendered = VARIANTS[variant](document)
    score = Score(document.name, variant)
    with tempfile.TemporaryDirectory() as tmp:
        path = _input_file(rendered, Path(tmp), f"{document.name}_{variant}")
        started = time.perf_counter()
        result = RedactionService().anonymize_file(path, path.name, rendered.content_type)
        score.seconds = time.perf_counter() - started
        output = Image.open(result.output_path)
        output_size = output.size
        output_pixels = np.asarray(output.convert("RGB"), dtype=np.int16)
        if keep_dir is not None:
            keep_dir.mkdir(parents=True, exist_ok=True)
            rendered.image.save(keep_dir / f"{document.name}_{variant}_input.png")
            output.convert("RGB").save(keep_dir / f"{document.name}_{variant}_redacted.png")
        output.close()
        Path(result.output_path).unlink(missing_ok=True)

    regions = _scale_regions(
        result.regions[0] if result.regions else [], output_size, rendered.image.size
    )
    sensitive = [b for b in rendered.boxes if b.seg.kind not in (None, KEEP)]
    score.sensitive = len(sensitive)
    source = _source_pixels(rendered)
    if source.shape == output_pixels.shape:
        hidden_share = _ink_hidden(
            source, output_pixels, _region_raster(regions, rendered.image.size)
        )
        shares = [hidden_share(b.x0, b.y0, b.x1, b.y1) for b in sensitive]
        hidden_words = [
            w.text for w in rendered.keep_words if hidden_share(w.x0, w.y0, w.x1, w.y1) > 0.3
        ]
    else:
        shares = [_covered(b, regions) for b in sensitive]
        hidden_words = [
            w.text
            for w in rendered.keep_words
            if _point_masked((w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2, regions)
        ]
    total = sum(b.area for b in sensitive)
    score.coverage = sum(s * b.area for s, b in zip(shares, sensitive, strict=True)) / total
    score.leaked = [
        f"{b.seg.text.strip()} ({b.seg.kind}, {s:.0%})"
        for b, s in zip(sensitive, shares, strict=True)
        if s < 0.95
    ]
    score.hidden = hidden_words
    masked_area = sum(r["szerokosc"] * r["wysokosc"] for r in regions)
    intended = sum(b.area for b in rendered.boxes if b.seg.kind != KEEP)
    page_area = rendered.image.size[0] * rendered.image.size[1]
    score.extra_area = max(0.0, masked_area - intended) / page_area
    return score


def _annotate(words: list[dict[str, Any]], boxes: list[Box]) -> list[dict[str, Any]]:
    annotated = []
    for word in words:
        x0, y0 = word["x"], word["y"]
        x1, y1 = x0 + word["szerokosc"], y0 + word["wysokosc"]
        area = max(1, word["szerokosc"] * word["wysokosc"])
        best = max(boxes, key=lambda b: _overlap(b, x0, y0, x1, y1))
        entry = dict(word)
        if any(c.isalnum() for c in word["tekst"]) and _overlap(best, x0, y0, x1, y1) / area >= 0.5:
            kind = best.seg.kind
            if kind == KEEP:
                entry["expect"] = "keep"
            elif kind is not None:
                entry["expect"] = "mask"
                entry["label"] = kind
                if best.seg.ner:
                    entry["needs_ner"] = True
                if kind in VISUAL_LABELS:
                    entry["visual"] = True
        annotated.append(entry)
    return annotated


def write_fixtures(target: Path, variants: list[str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    adapter = LocalOCRAdapter()
    for document in CORPUS:
        for variant in variants:
            rendered = VARIANTS[variant](document)
            with tempfile.TemporaryDirectory() as tmp:
                path = _input_file(rendered, Path(tmp), document.name)
                if rendered.pdf is not None:
                    pages, _ = adapter.ocr_pdf(path)
                else:
                    pages, _ = adapter.ocr_image(path)
            words = _annotate(pages[0], rendered.boxes)
            lines = ",\n".join(json.dumps(w, ensure_ascii=False) for w in words)
            header = {
                "document": document.name,
                "language": document.language,
                "variant": variant,
                "source": "local_tesseract",
                "width": rendered.image.size[0],
                "height": rendered.image.size[1],
            }
            body = json.dumps(header, ensure_ascii=False)[:-1]
            out = target / f"{document.name}_{variant}.ocr.json"
            out.write_text(f'{body}, "words": [\n{lines}\n]}}\n', encoding="utf-8")
            print(f"  {out} ({len(words)} words)")


def _print_scores(scores: list[Score]) -> None:
    print(
        f"\n{'document':<24}{'variant':<9}{'coverage':>9}{'leaks':>7}{'hidden':>8}{'extra':>8}{'sec':>7}"
    )
    print("-" * 72)
    for s in scores:
        print(
            f"{s.document:<24}{s.variant:<9}{s.coverage:>9.1%}{len(s.leaked):>4}/{s.sensitive:<3}"
            f"{len(s.hidden):>6}{s.extra_area:>8.2%}{s.seconds:>7.1f}"
        )
    print("-" * 72)
    for variant in dict.fromkeys(s.variant for s in scores):
        part = [s for s in scores if s.variant == variant]
        coverage = sum(s.coverage for s in part) / len(part)
        leaks = sum(len(s.leaked) for s in part)
        total = sum(s.sensitive for s in part)
        hidden = sum(len(s.hidden) for s in part)
        print(f"{'ALL':<24}{variant:<9}{coverage:>9.1%}{leaks:>4}/{total:<3}{hidden:>6}")
    print("\nNot covered (below 95%) and hidden required text:")
    for s in scores:
        for item in s.leaked:
            print(f"  LEAK   {s.document}/{s.variant}: {item}")
        for item in s.hidden:
            print(f"  HIDDEN {s.document}/{s.variant}: {item}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure anonymization on a synthetic corpus.")
    parser.add_argument("--documents", default="", help="comma separated names, default all")
    parser.add_argument("--variants", default="scan,photo,rot90,rot180,lowres,pdf")
    parser.add_argument("--keep-files", type=Path, default=None)
    parser.add_argument("--write-fixtures", type=Path, default=None)
    args = parser.parse_args()

    variants = [v for v in args.variants.split(",") if v]
    unknown = set(variants) - set(VARIANTS)
    if unknown:
        parser.error(f"Unknown variants: {', '.join(sorted(unknown))}")

    if args.write_fixtures is not None:
        write_fixtures(args.write_fixtures, variants)
        return

    names = {d for d in args.documents.split(",") if d}
    documents = [d for d in CORPUS if not names or d.name in names]
    scores = [evaluate(d, v, args.keep_files) for d in documents for v in variants]
    _print_scores(scores)


if __name__ == "__main__":
    main()
