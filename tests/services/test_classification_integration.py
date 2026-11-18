from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.enums.document import DocumentType

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MODEL_PATH = _PROJECT_ROOT / "storage" / "classifier_model.joblib"


pytestmark = pytest.mark.skipif(
    not _MODEL_PATH.exists(),
    reason=f"Trained classifier model not found at {_MODEL_PATH}. "
    "Run `python scripts/train_classifier.py` first.",
)


@pytest.fixture(scope="module")
def classifier_service(monkeypatch_module):
    monkeypatch_module.setenv("CLASSIFIER_MODEL_PATH", str(_MODEL_PATH))
    monkeypatch_module.setenv("APP_SECRET_KEY", "test")
    monkeypatch_module.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch_module.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.com")
    monkeypatch_module.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "x")

    module = importlib.import_module("app.services.classification")
    importlib.reload(module)
    module.ClassificationService._model = None
    module.ClassificationService._vectorizer = None
    module.ClassificationService._classes = []
    return module.ClassificationService()


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


_CASES: list[tuple[DocumentType, str]] = [
    (
        DocumentType.INVOICE,
        "Faktura VAT FV/2024/03/118 nabywca NIP 1234567890 netto VAT 23% brutto "
        "termin płatności przelew IBAN",
    ),
    (
        DocumentType.CONTRACT,
        "This Agreement is entered into as of the Effective Date by and between "
        "the Parties confidentiality intellectual property termination governing law",
    ),
    (
        DocumentType.PATENT,
        "Patent claim invention embodiment prior art field of invention "
        "assignee abstract apparatus method",
    ),
    (
        DocumentType.LAWSUIT,
        "Pozew o zapłatę do Sądu Rejonowego, powód, pozwany, wartość przedmiotu sporu, "
        "wnoszę o zasądzenie kwoty wraz z kosztami procesu",
    ),
    (
        DocumentType.POWER_OF_ATTORNEY,
        "Pełnomocnictwo. Mocodawca upoważnia pełnomocnika do reprezentowania go przed sądem "
        "z prawem udzielania dalszych pełnomocnictw",
    ),
    (
        DocumentType.APPLICATION,
        "Curriculum Vitae, doświadczenie zawodowe, wykształcenie, umiejętności, "
        "wyrażam zgodę na przetwarzanie danych w procesie rekrutacji",
    ),
    (
        DocumentType.DEMAND_LETTER,
        "Przedsądowe wezwanie do zapłaty. Wzywam dłużnika do zapłaty zaległości na rzecz "
        "wierzyciela w terminie 7 dni, w przeciwnym razie sprawa trafi do sądu",
    ),
]


@pytest.mark.slow
@pytest.mark.parametrize(("expected", "text"), _CASES)
def test_real_model_classifies_expected_type(
    classifier_service, expected: DocumentType, text: str
) -> None:
    doc_type, confidence, scores = classifier_service.classify(text)

    assert doc_type == expected, (
        f"Expected {expected}, got {doc_type} (confidence={confidence:.2%}). "
        f"Top scores: {sorted(scores.items(), key=lambda x: -x[1])[:3]}"
    )
    assert confidence > 0.0
    assert expected.value in scores


@pytest.mark.slow
def test_real_model_returns_unknown_for_gibberish(classifier_service) -> None:
    doc_type, confidence, _ = classifier_service.classify("hello")
    msg = f"Expected low confidence for empty content, got {confidence:.2%} -> {doc_type}"
    assert confidence < 0.6, msg
