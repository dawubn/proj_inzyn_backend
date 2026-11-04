"""Tests for locating formal (missing element) issues on document pages."""

from __future__ import annotations

from typing import Any

from app.enums.analysis import ValidationSeverity
from app.schemas.analysis_report import ValidationIssue
from app.services.formal_issue_locator import locate_formal_issues


def _issue(field_name: str) -> ValidationIssue:
    return ValidationIssue(
        rule_name=field_name,
        field_name=field_name,
        severity=ValidationSeverity.ERROR,
        message=f"missing {field_name}",
        details={"expected_field": field_name},
    )


def _page_words(page: int, lines: list[tuple[float, str]]) -> list[dict[str, Any]]:
    """Words of lines given as (normalized y, text)."""
    words: list[dict[str, Any]] = []
    for line_no, (y, text) in enumerate(lines):
        for i, token in enumerate(text.split()):
            words.append(
                {
                    "text": token,
                    "nbbox": {"x": 0.1 + 0.1 * i, "y": y, "width": 0.08, "height": 0.02},
                    "line": f"1-1-{line_no}",
                    "page": page,
                }
            )
    return words


def _location(issues: list[ValidationIssue], field_name: str) -> dict[str, Any] | None:
    issue = next(i for i in issues if i.field_name == field_name)
    return issue.details.get("location")


WORDS = _page_words(1, [(0.1, "Pozew o rozwód"), (0.2, "......, dnia ......")]) + _page_words(
    2, [(0.1, "Uzasadnienie"), (0.5, "ostatnia linia tekstu")]
)


def test_date_points_to_placeholder_line() -> None:
    located = locate_formal_issues([_issue("has_date")], WORDS)

    assert _location(located, "has_date") == {
        "kind": "placeholder",
        "page": 1,
        "rects": [{"x": 0.1, "y": 0.2, "width": 0.28, "height": 0.02}],
    }


def test_date_without_placeholder_uses_top_of_first_page() -> None:
    words = _page_words(1, [(0.3, "Umowa najmu")])

    location = _location(locate_formal_issues([_issue("has_date")], words), "has_date")

    assert location is not None
    assert location["kind"] == "expected"
    assert location["page"] == 1
    assert location["rects"][0]["y"] < 0.15


def test_signature_and_attachments_stack_below_last_line_of_last_text_page() -> None:
    blank_last_page = [{"text": "", "nbbox": {}, "line": "0", "page": 3}]
    located = locate_formal_issues(
        [_issue("has_signature"), _issue("has_attachments_section")], WORDS + blank_last_page
    )

    signature = _location(located, "has_signature")
    attachments = _location(located, "has_attachments_section")

    assert signature is not None and attachments is not None
    assert signature["kind"] == attachments["kind"] == "expected"
    assert signature["page"] == attachments["page"] == 2
    assert signature["rects"][0]["y"] == 0.535
    assert attachments["rects"][0]["y"] > signature["rects"][0]["y"]


def test_band_stays_inside_page_when_text_ends_at_bottom() -> None:
    words = _page_words(1, [(0.97, "stopka")])

    location = _location(locate_formal_issues([_issue("has_signature")], words), "has_signature")

    assert location is not None
    rect = location["rects"][0]
    assert rect["y"] + rect["height"] <= 1


def test_sections_point_to_page_without_rects() -> None:
    location = _location(
        locate_formal_issues([_issue("has_parties_section")], WORDS), "has_parties_section"
    )

    assert location == {"kind": "page", "page": 1, "rects": []}


def test_keeps_existing_details_and_skips_unknown_fields() -> None:
    located = locate_formal_issues([_issue("has_date"), _issue("has_text")], WORDS)

    assert located[0].details["expected_field"] == "has_date"
    assert "location" not in located[1].details


def test_no_locations_without_words() -> None:
    issues = [_issue("has_date")]

    assert locate_formal_issues(issues, []) == issues


def test_common_word_without_blank_is_not_a_placeholder() -> None:
    words = _page_words(1, [(0.3, "płatny do 10 dnia każdego miesiąca")])

    location = _location(locate_formal_issues([_issue("has_date")], words), "has_date")

    assert location is not None
    assert location["kind"] == "expected"


def test_template_label_is_a_placeholder_without_blank() -> None:
    words = _page_words(1, [(0.4, "(miejscowość i data)")])

    location = _location(locate_formal_issues([_issue("has_date")], words), "has_date")

    assert location is not None
    assert location["kind"] == "placeholder"
    assert location["rects"][0]["y"] == 0.4
