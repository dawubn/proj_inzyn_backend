import importlib

import joblib
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from app.core.exceptions import ClassifierNotReadyError
from app.enums.document import DocumentType


def _build_test_model(path: str) -> None:
    train_texts = [
        "balance sheet revenue profit financial report",
        "quarterly earnings statement assets liabilities",
        "patent claim invention embodiment prior art",
        "patent filing method system described",
        "instruction manual installation guide troubleshooting",
        "user manual setup procedure warning",
    ]
    train_labels = [
        "financial_reports",
        "financial_reports",
        "patents",
        "patents",
        "manuals",
        "manuals",
    ]

    vectorizer = TfidfVectorizer(max_features=200)
    x_train = vectorizer.fit_transform(train_texts)
    model = LogisticRegression(max_iter=500, C=5.0)
    model.fit(x_train, train_labels)

    payload = {
        "model": model,
        "vectorizer": vectorizer,
        "classes": model.classes_,
    }
    joblib.dump(payload, path)


def _set_required_env(monkeypatch: pytest.MonkeyPatch, model_path: str) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://test.example.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "test")
    monkeypatch.setenv("CLASSIFIER_MODEL_PATH", model_path)
    monkeypatch.setenv("CLASSIFIER_MIN_CONFIDENCE", "0.0")
    # Patch the already-instantiated settings singleton so the service picks up the correct path
    import app.core.config as _cfg

    monkeypatch.setattr(_cfg.settings, "CLASSIFIER_MODEL_PATH", model_path)
    monkeypatch.setattr(_cfg.settings, "CLASSIFIER_MIN_CONFIDENCE", 0.0)


def test_classification_service_returns_mapped_document_type(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = str(tmp_path / "classifier_model.joblib")
    _build_test_model(model_path)
    _set_required_env(monkeypatch, model_path)

    classification_module = importlib.import_module("app.services.classification")
    importlib.reload(classification_module)
    classification_module.ClassificationService._model = None
    classification_module.ClassificationService._vectorizer = None
    classification_module.ClassificationService._classes = []

    svc = classification_module.ClassificationService()

    doc_type, confidence, scores = svc.classify("patent claim invention method")

    assert doc_type == DocumentType.PATENT
    assert 0.0 <= confidence <= 1.0
    assert "patent" in scores


def test_classification_service_raises_when_model_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_path = str(tmp_path / "missing_model.joblib")
    _set_required_env(monkeypatch, missing_path)

    classification_module = importlib.import_module("app.services.classification")
    importlib.reload(classification_module)
    classification_module.ClassificationService._model = None
    classification_module.ClassificationService._vectorizer = None
    classification_module.ClassificationService._classes = []

    svc = classification_module.ClassificationService()

    with pytest.raises(ClassifierNotReadyError):
        svc.classify("financial statement")
