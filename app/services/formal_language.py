"""Small, deterministic Polish language rules over Azure text only.

These rules establish explicit absence and affirmative actions, not legal
validity. Morphological stems accept inflections; negation is clause-scoped.
No entity inference is made from the original document or local OCR.
"""

import re

from app.services.formal_context import normalize

_ABSENCE = re.compile(
    r"^(?:brak(?=\s*$|[.;,:])|brak\s+(?:danych|daty|zakresu|podpisu|tresci)|nie\s+(?:podano|wskazano|wpisano|uzupelniono)|"
    r"nie dotyczy|do uzupelnienia|wpisz\s+(?:dane|imie|nazwisko)|"
    r"dane\s+(?:zostana|beda)\s+uzupelnione)\b"
)
_ACTION = re.compile(
    r"\b(?:reprezent\w*|odbior\w*|odbier\w*|sklad\w*|zlozen\w*|prowadzen\w*|"
    r"zawar\w*|podpis\w*|zakup\w*|sprzedaz\w*|czynnosci\s+\w+)"
)
_NEGATION = re.compile(r"\b(?:nie(?!\s+tylko\b)|bez\s+(?:prawa|uprawnien)|zakaz\w*)\b")
_POSTPOSED_PROHIBITION = re.compile(
    r"\b(?:(?:jest|sa|pozostaje|pozostaja)\s+(?:zabronion\w*|zakazan\w*|niedozwolon\w*)"
    r"|nie\s+(?:jest|sa)\s+(?:dozwolon\w*|dopuszczaln\w*)|nie\s+wolno)\b"
)
_CLAUSE = re.compile(r"[.;!?\n]+|\b(?:ale|natomiast|jednak|lecz)\b")


def explicit_absence(value: str) -> bool:
    text = normalize(value).strip()
    # Only a whole placeholder is a template. A real value in brackets is valid.
    placeholder = re.fullmatch(
        r"[\[<(]\s*(imie i nazwisko|dane osoby|nazwa firmy)\s*[\]>)\.]", text
    )
    return bool(placeholder or _ABSENCE.match(text))


def has_affirmative_action(value: str) -> bool:
    for clause in _CLAUSE.split(normalize(value)):
        for action in _ACTION.finditer(clause):
            if not _NEGATION.search(clause[: action.start()]) and not _POSTPOSED_PROHIBITION.search(
                clause[action.end() :]
            ):
                return True
    return False
