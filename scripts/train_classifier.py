from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report

from app.utils.text_normalize import normalize_text

DEFAULT_DATA_DIR = "storage/classifier_training"
DEFAULT_MODEL_OUTPUT = "storage/classifier_model.joblib"


def load_dataset(data_dir: str, split_name: str) -> tuple[list[str], list[str]]:
    split_dir = os.path.join(data_dir, split_name)
    texts: list[str] = []
    labels: list[str] = []

    print(f"\n[STEP] Loading texts from split: {split_name.upper()}...")

    if not os.path.exists(split_dir):
        print(f"[ERROR] Directory does not exist: {split_dir}")
        return texts, labels

    classes = [d for d in os.listdir(split_dir) if os.path.isdir(os.path.join(split_dir, d))]
    for class_name in sorted(classes):
        class_dir = os.path.join(split_dir, class_name)
        txt_files = [f for f in os.listdir(class_dir) if f.lower().endswith(".txt")]
        loaded = 0
        for file_name in txt_files:
            txt_path = os.path.join(class_dir, file_name)
            try:
                with open(txt_path, encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        texts.append(content)
                        labels.append(class_name)
                        loaded += 1
            except Exception as e:
                print(f"[ERROR] Cannot read {txt_path}: {e}")
        print(f"  '{class_name}': {loaded} documents")

    return texts, labels


def _normalize_corpus(text_list: list[str]) -> list[str]:
    return [normalize_text(t) for t in text_list]


def train_and_save(data_dir: str, output_path: str) -> None:
    train_texts, train_labels = load_dataset(data_dir, "train")
    valid_texts, valid_labels = load_dataset(data_dir, "valid")
    train_texts = _normalize_corpus(train_texts)
    valid_texts = _normalize_corpus(valid_texts)

    if not train_texts or not valid_texts:
        print("\n[FATAL] No training data found. Aborting.")
        sys.exit(1)

    print(f"\n[INFO] Train: {len(train_texts)} documents")
    print(f"[INFO] Valid: {len(valid_texts)} documents")

    print("\n[STEP] Vectorising (TF-IDF)...")
    vectorizer = TfidfVectorizer(max_features=5000)
    X_train = vectorizer.fit_transform(train_texts)
    X_valid = vectorizer.transform(valid_texts)

    print("[STEP] Training Logistic Regression...")
    model = LogisticRegression(max_iter=2000, C=5.0, verbose=1)
    model.fit(X_train, train_labels)
    print("\n[STEP] Training done.")

    print("[STEP] Evaluating on validation set...")
    y_pred = model.predict(X_valid)

    print("\n" + "=" * 50)
    print(f"Accuracy: {accuracy_score(valid_labels, y_pred):.4f}\n")
    print(classification_report(valid_labels, y_pred))
    print("=" * 50)

    print(f"\n[STEP] Saving model to: {output_path}")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    payload = {
        "model": model,
        "vectorizer": vectorizer,
        "classes": model.classes_,
    }
    joblib.dump(payload, output_path)
    print(f"[OK] Model saved: {output_path}")
    print(f"[OK] Classes: {list(model.classes_)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train document classifier.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", default=DEFAULT_MODEL_OUTPUT)
    args = parser.parse_args()

    print("=" * 50)
    print(" DOCUMENT CLASSIFIER TRAINING")
    print("=" * 50)
    print(f" Data:   {args.data_dir}")
    print(f" Output: {args.output}")

    train_and_save(args.data_dir, args.output)
