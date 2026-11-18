from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import structlog

from app.core.config import settings
from app.core.exceptions import ClassifierNotReadyError
from app.enums.document import DocumentType
from app.utils.text_normalize import normalize_text

logger = structlog.get_logger(__name__)

_LABEL_TO_DOCUMENT_TYPE: dict[str, DocumentType] = {
    "contracts": DocumentType.CONTRACT,
    "invoices": DocumentType.INVOICE,
    "patents": DocumentType.PATENT,
    "lawsuits": DocumentType.LAWSUIT,
    "power_of_attorney": DocumentType.POWER_OF_ATTORNEY,
    "applications": DocumentType.APPLICATION,
    "demand_letters": DocumentType.DEMAND_LETTER,
}


class ClassificationService:
    _model: Any = None
    _vectorizer: Any = None
    _classes: list[str] = []

    def _load_model(self) -> None:
        model_path = Path(settings.CLASSIFIER_MODEL_PATH)
        if not model_path.exists():
            raise ClassifierNotReadyError(
                f"Classifier model file does not exist: {model_path}. "
                "Run scripts/train_classifier.py to train it."
            )

        payload = joblib.load(model_path)
        ClassificationService._model = payload["model"]
        ClassificationService._vectorizer = payload["vectorizer"]
        ClassificationService._classes = [str(label) for label in payload["classes"]]

        logger.info(
            "Classifier model loaded",
            path=str(model_path),
            classes=ClassificationService._classes,
        )

    def classify(self, text: str) -> tuple[DocumentType, float, dict[str, float]]:
        if ClassificationService._model is None or ClassificationService._vectorizer is None:
            self._load_model()

        cleaned = normalize_text(text)
        features = ClassificationService._vectorizer.transform([cleaned])

        predicted_label: str = ClassificationService._model.predict(features)[0]
        probabilities: list[float] = list(ClassificationService._model.predict_proba(features)[0])

        confidence = float(max(probabilities))
        all_scores: dict[str, float] = {}
        for label, prob in zip(ClassificationService._classes, probabilities, strict=False):
            mapped_label = _LABEL_TO_DOCUMENT_TYPE.get(label, DocumentType.UNKNOWN).value
            all_scores[mapped_label] = round(float(prob), 4)

        document_type = _LABEL_TO_DOCUMENT_TYPE.get(predicted_label, DocumentType.UNKNOWN)

        if confidence < settings.CLASSIFIER_MIN_CONFIDENCE:
            logger.info(
                "Classification below confidence threshold",
                predicted=predicted_label,
                confidence=round(confidence, 4),
                threshold=settings.CLASSIFIER_MIN_CONFIDENCE,
            )
            document_type = DocumentType.UNKNOWN

        logger.info(
            "Document classified",
            label=predicted_label,
            document_type=document_type,
            confidence=round(confidence, 4),
        )

        return document_type, confidence, all_scores
