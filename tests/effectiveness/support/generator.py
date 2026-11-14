"""Generate synthetic PDFs with explicit field-level ground truth."""

from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from .catalog import (
    GRAMMAR_MUTATIONS,
    INVALID_DATES,
    PEOPLE,
    get_fields,
    get_title,
    is_scored,
    resolve_document_types,
)
from .models import (
    DefectKind,
    ExpectedField,
    GeneratedCase,
    GeneratedDefect,
    JsonObject,
    Outcome,
    RunConfig,
)

GENERATOR_VERSION = "2.0"
FONT_NAME = "EffectivenessDejaVu"
BOLD_FONT_NAME = "EffectivenessDejaVuBold"

# Keep both mutation styles. Removing difficult cases would inflate recall.
MISSING_STYLES = ("omitted", "blank")

# These optional sections can repeat the primary financial figures.
DEPENDENT_OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "has_financial_data": (
        "has_balance_sheet",
        "has_income_statement",
    ),
}


def _register_fonts() -> None:
    from matplotlib import get_data_path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    directory = Path(get_data_path()) / "fonts" / "ttf"

    for name, filename in (
        (FONT_NAME, "DejaVuSans.ttf"),
        (BOLD_FONT_NAME, "DejaVuSans-Bold.ttf"),
    ):
        if name not in pdfmetrics.getRegisteredFontNames():
            path = directory / filename
            if not path.is_file():
                raise FileNotFoundError(f"PDF font not found: {path}")
            pdfmetrics.registerFont(TTFont(name, str(path)))


def _money(grosze: int) -> str:
    return f"{grosze // 100},{grosze % 100:02d} PLN"


def _document_title(document_type: str) -> str:
    # The form code belongs only to has_tax_form, never to the heading.
    if document_type == "tax_form":
        return "DEKLARACJA PODATKOWA — PRZYKŁAD TESTOWY"
    return get_title(document_type)


def _make_values(
    rng: random.Random,
    index: int,
    document_type: str,
) -> dict[str, str]:
    person, attorney = rng.sample(PEOPLE, 2)
    issued = date(2026, 1, 1) + timedelta(days=rng.randrange(280))
    issued_text = issued.strftime("%d.%m.%Y")
    net = rng.randrange(100, 15001) * 100
    gross = _money(net + net * 23 // 100)

    values: dict[str, str] = {
        "has_date": issued_text,
        "has_principal_section": person,
        "has_attorney_section": attorney,
        "has_authorization_scope_section": rng.choice(
            (
                "reprezentowanie przed sądem rejonowym",
                "odbiór korespondencji w imieniu mocodawcy",
                "reprezentowanie przed organami administracji",
            )
        ),
        "has_parties_section": f"Powód: {person}. Pozwany: {attorney}.",
        "has_subject_section": (
            "Wykonanie przeglądu instalacji pomiarowej i przekazanie protokołu."
        ),
        "has_court_section": "Sąd Rejonowy w Testowie, Wydział Cywilny",
        "has_claim_section": f"Wnoszę o zasądzenie kwoty {gross} od pozwanego na rzecz powoda.",
        "has_justification_section": (
            "Powód wykonał uzgodnioną usługę. Pozwany odebrał protokół, "
            "ale nie zapłacił wynagrodzenia."
        ),
        "has_applicant": person,
        "has_recipient": "Urząd Miasta Testowo",
        "has_request": "Wnoszę o wydanie zaświadczenia o udziale w demonstracyjnym szkoleniu.",
        "has_application_date": issued_text,
        "has_application_justification": "Dokumentacja jest potrzebna do celów szkoleniowych.",
        "has_invoice_number": f"TEST/{issued.year}/{index:04d}",
        "has_issue_date": issued_text,
        "has_seller": "Modelowe Usługi spółka z o.o.",
        "has_buyer": "Pracownia Pomiarowa spółka z o.o.",
        "has_invoice_items": (
            f"Przegląd instalacji pomiarowej; ilość: 1; cena netto: {_money(net)}"
        ),
        "has_invoice_total": gross,
        "has_tax_details": "23%",
        "has_surname": person.split()[-1],
        "has_given_names": person.split()[0],
        "has_birth_date": "12.05.1990",
        "has_document_number": f"TEST{index:06d}",
        "has_expiry_date": "12.05.2030",
        "has_issuing_authority": "Organ demonstracyjny Testowo",
        "has_pesel": "90051212345",
        "has_nationality": "polskie",
        "has_bank": "Bank Demonstracyjny",
        "has_account_holder": person,
        "has_account_number": "00123456789012345678901234",
        "has_statement_period": "01.01.2026 - 31.01.2026",
        "has_opening_balance": "1000,00 PLN",
        "has_closing_balance": "1500,00 PLN",
        "has_transactions": "05.01.2026 — Uznanie rachunku: 500,00 PLN",
        "has_tax_form": "PIT-37",
        "has_taxpayer": person,
        "has_tax_identifier": "1234567890",
        "has_tax_period": "2025",
        "has_tax_office": "Urząd Skarbowy w Testowie",
        "has_tax_values": "1200,00 PLN",
        "has_reporting_entity": "Pracownia Modelowa spółka z o.o.",
        "has_reporting_period": "01.01.2025 - 31.12.2025",
        "has_financial_data": "150000,00 PLN",
        "has_balance_sheet": "Aktywa trwałe: 100000,00 PLN. Aktywa obrotowe: 50000,00 PLN.",
        "has_income_statement": (
            "Przychody: 200000,00 PLN. Koszty: 180000,00 PLN. Wynik finansowy: 20000,00 PLN."
        ),
        "has_financial_notes": "Zastosowano demonstracyjne zasady rachunkowości.",
        "has_contracting_authority": "Laboratorium Demonstracyjne w Testowie",
        "has_tender_subject": (
            "Dostawa dziesięciu czujników temperatury do laboratorium demonstracyjnego."
        ),
        "has_tender_reference": f"TEST/ZAM/{index:04d}",
        "has_submission_deadline": "30.11.2026",
        "has_submission_method": (
            "Oferty należy przesłać elektronicznie na adres "
            "[przetarg@example.invalid](mailto:przetarg@example.invalid)."
        ),
        "has_award_criteria": "Cena: 80%. Termin realizacji: 20%.",
        "has_act_title": "Regulamin udostępniania sal demonstracyjnych",
        "has_act_date": issued_text,
        "has_provisions": (
            "§ 1. Sale udostępnia się na szkolenia demonstracyjne. "
            "§ 2. Użytkownik pozostawia salę w stanie uporządkowanym."
        ),
        "has_publication_reference": f"TEST/REG/{index:04d}",
        "has_effective_date": "Regulamin stosuje się od 01.12.2026.",
        "has_manual_subject": f"Czujnik temperatury TEST-{index:04d}",
        "has_operating_instructions": (
            "Podłącz przewód do złącza zasilania. "
            "Naciśnij przycisk uruchomienia. "
            "Odczytaj temperaturę z wyświetlacza."
        ),
        "has_safety_instructions": (
            "Ryzyko porażenia prądem elektrycznym. Urządzenie nie jest odporne na działanie wody."
        ),
        "has_manual_version": "TEST-1.0",
        "has_invention_title": "Moduł stabilizacji temperatury komory pomiarowej",
        "has_patent_applicant": person,
        "has_invention_description": (
            "Układ zawiera czujnik, regulator i element grzejny. "
            "Regulator porównuje pomiar z wartością zadaną "
            "i dostosowuje moc grzania."
        ),
        "has_patent_claims": (
            "Układ znamienny tym, że regulator steruje elementem "
            "grzejnym na podstawie różnicy temperatur."
        ),
        "has_patent_abstract": "Rozwiązanie ogranicza wahania temperatury w komorze pomiarowej.",
        "has_patent_number": f"TEST/PL/{index:06d}",
        "has_article_title": "Porównanie stabilności wskazań czujników temperatury",
        "has_authors": f"{person}, {attorney}",
        "has_article_content": (
            "Porównano trzy czujniki w stałych warunkach. "
            "Każdy pomiar powtórzono dziesięć razy. "
            "Oceniono rozrzut wskazań względem wartości odniesienia."
        ),
        "has_article_abstract": (
            "Przedstawiono demonstracyjną metodę porównania stabilności pomiaru temperatury."
        ),
        "has_references": "Metody pomiarowe, opracowanie demonstracyjne, 2024.",
    }

    if document_type == "contract":
        values["has_parties_section"] = f"Zamawiający: {person}. Wykonawca: {attorney}."

    return values


def _select_defects(
    rng: random.Random,
    config: RunConfig,
    document_type: str,
) -> tuple[GeneratedDefect, ...]:
    options: dict[str, tuple[DefectKind, ...]] = {}

    for item in get_fields(document_type):
        if not is_scored(item):
            continue

        kinds: list[DefectKind] = []

        if DefectKind.MISSING in config.defect_kinds:
            kinds.append(DefectKind.MISSING)

        if DefectKind.INVALID_DATE in config.defect_kinds and item.kind == "date":
            kinds.append(DefectKind.INVALID_DATE)

        if kinds:
            options[item.key] = tuple(kinds)

    if DefectKind.GRAMMAR in config.defect_kinds:
        options["__grammar__"] = (DefectKind.GRAMMAR,)

    if config.min_defects > len(options):
        raise ValueError(
            f"{document_type}: min_defects={config.min_defects} "
            f"exceeds mutation capacity {len(options)}."
        )

    count = rng.randint(
        config.min_defects,
        min(config.max_defects, len(options)),
    )
    defects: list[GeneratedDefect] = []

    for key in rng.sample(list(options), count):
        kind = rng.choice(options[key])

        if kind == DefectKind.GRAMMAR:
            defect = GeneratedDefect(
                kind=kind,
                description=rng.choice(GRAMMAR_MUTATIONS),
            )
        elif kind == DefectKind.INVALID_DATE:
            defect = GeneratedDefect(
                kind=kind,
                field_key=key,
                description=rng.choice(INVALID_DATES),
                mutation_style="invalid_value",
            )
        else:
            defect = GeneratedDefect(
                kind=kind,
                field_key=key,
                description=f"Missing field: {key}",
                mutation_style=rng.choice(MISSING_STYLES),
            )

        defects.append(defect)

    return tuple(defects)


def _suppressed_fields(
    defects: tuple[GeneratedDefect, ...],
) -> set[str]:
    return {
        dependent
        for defect in defects
        if defect.kind == DefectKind.MISSING and defect.field_key is not None
        for dependent in DEPENDENT_OPTIONAL_FIELDS.get(defect.field_key, ())
    }


def _make_expectations(
    document_type: str,
    defects: tuple[GeneratedDefect, ...],
) -> tuple[ExpectedField, ...]:
    mutations = {item.field_key: item for item in defects if item.field_key is not None}
    suppressed = _suppressed_fields(defects)
    expected: list[ExpectedField] = []

    for item in get_fields(document_type):
        defect = mutations.get(item.key)
        scored = is_scored(item)
        absence_scored = scored and (defect is None or defect.kind == DefectKind.MISSING)
        outcome = Outcome.FAIL if defect is not None else Outcome.PASS
        reason: str | None

        if item.key in suppressed:
            reason = (
                "Optional section suppressed to avoid repeating intentionally missing primary data."
            )
        elif item.kind in {"signature", "visual"}:
            reason = "Signature or visual inspection is not scored."
        elif item.advisory:
            reason = "Optional or advisory field is excluded."
        elif not absence_scored:
            reason = "Invalid dates are excluded from absence metrics."
        else:
            reason = None

        expected.append(
            ExpectedField(
                field_key=item.key,
                label=item.label,
                expected=outcome if scored else Outcome.UNKNOWN,
                defect_kind=defect.kind if defect else None,
                scored=scored,
                absence_scored=absence_scored,
                exclusion_reason=reason,
            )
        )

    return tuple(expected)


def _render_pdf(
    path: Path,
    case: GeneratedCase,
    values: dict[str, str],
    layout: str,
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_fonts()

    body = ParagraphStyle(
        "Body",
        fontName=FONT_NAME,
        fontSize=10,
        leading=15,
        spaceAfter=8,
    )
    heading = ParagraphStyle(
        "Heading",
        parent=body,
        fontName=BOLD_FONT_NAME,
        fontSize=15,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748B"),
    )

    title = _document_title(case.document_type)
    mutations = {item.field_key: item for item in case.defects if item.field_key is not None}
    suppressed = _suppressed_fields(case.defects)

    story = [
        Paragraph(escape(title), heading),
        Paragraph(
            "MATERIAŁ TESTOWY — FIKCYJNE DANE. "
            "Dokument nie jest przeznaczony do użytku urzędowego.",
            small,
        ),
        Spacer(1, 4 * mm),
    ]

    for item in get_fields(case.document_type):
        if item.kind == "visual" or item.key in suppressed:
            continue

        defect = mutations.get(item.key)

        if (
            defect is not None
            and defect.kind == DefectKind.MISSING
            and defect.mutation_style == "omitted"
        ):
            continue

        if item.kind == "signature" or (defect is not None and defect.kind == DefectKind.MISSING):
            value = "................................"
        elif defect is not None and defect.kind == DefectKind.INVALID_DATE:
            value = defect.description
        else:
            if item.key not in values:
                raise ValueError(f"No value for {case.document_type}.{item.key}.")
            value = values[item.key]

        separator = "<br/>" if layout == "stacked" else ": "
        content = Paragraph(
            f"<b>{escape(item.label)}</b>{separator}{escape(value)}",
            body,
        )

        if layout == "boxed":
            box = Table([[content]], colWidths=[170 * mm])
            box.setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.extend((box, Spacer(1, 2 * mm)))
        else:
            story.append(content)

    for defect in case.defects:
        if defect.kind == DefectKind.GRAMMAR:
            story.extend(
                (
                    Spacer(1, 3 * mm),
                    Paragraph(escape(defect.description), body),
                )
            )

    SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Effectiveness benchmark",
    ).build(story)


def generate_documents(
    output_directory: Path,
    config: RunConfig,
) -> list[GeneratedCase]:
    selected = resolve_document_types(config.document_types)

    # Reproducible synthetic test data, not security-sensitive randomness.
    rng = random.Random(config.seed)  # noqa: S311

    types = [selected[index % len(selected)] for index in range(config.document_count)]
    rng.shuffle(types)

    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    planned = [
        output_directory / f"document_{index:03d}_{document_type}.pdf"
        for index, document_type in enumerate(types, 1)
    ]

    for path in planned:
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite: {path}")

    cases: list[GeneratedCase] = []

    for index, (document_type, path) in enumerate(zip(types, planned, strict=True), 1):
        values = _make_values(rng, index, document_type)
        defects = _select_defects(rng, config, document_type)
        layout = rng.choice(("inline", "stacked", "boxed"))

        metadata: JsonObject = {
            "generator_version": GENERATOR_VERSION,
            "index": index,
            "layout": layout,
            "seed": config.seed,
            "defect_count": len(defects),
            "suppressed_optional_fields": sorted(_suppressed_fields(defects)),
            "synthetic_identifiers": True,
        }

        case = GeneratedCase(
            case_id=path.stem,
            document_type=document_type,
            filename=path.name,
            expected_fields=_make_expectations(document_type, defects),
            defects=defects,
            metadata=metadata,
        )

        temporary = path.with_suffix(".pdf.tmp")

        try:
            _render_pdf(temporary, case, values, layout)
            metadata["sha256"] = hashlib.sha256(temporary.read_bytes()).hexdigest()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

        cases.append(case)

    return cases
