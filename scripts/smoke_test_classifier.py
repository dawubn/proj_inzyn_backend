import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("APP_SECRET_KEY", "smoke")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://example.com")
os.environ.setdefault("AZURE_DOCUMENT_INTELLIGENCE_KEY", "x")
os.environ.setdefault("CLASSIFIER_MODEL_PATH", "storage/classifier_model.joblib")

from app.services.classification import ClassificationService

SAMPLES_DIR = Path("/Users/piotr/Desktop/casifier/DocLayNet_extraSplit_Txt/valid")


def main() -> None:
    svc = ClassificationService()

    if not SAMPLES_DIR.exists():
        print(f"[INFO] Samples directory not found: {SAMPLES_DIR}")
        print("       Change SAMPLES_DIR or run the manual API smoke test instead.")
        return

    print("\nClassifier smoke test against real DocLayNet documents (valid):")
    print("-" * 78)
    print(f"{'File':<55} {'Prediction':<20} {'Confidence':<10}")
    print("-" * 78)

    total, correct = 0, 0
    for class_dir in sorted(p for p in SAMPLES_DIR.iterdir() if p.is_dir()):
        txt_files = sorted(class_dir.glob("*.txt"))
        if not txt_files:
            continue

        sample = txt_files[0]
        text = sample.read_text(encoding="utf-8").strip()
        doc_type, confidence, _ = svc.classify(text)

        expected = {
            "financial_reports": "financial_report",
            "government_tenders": "government_tender",
            "laws_and_regulations": "law_and_regulation",
            "manuals": "manual",
            "patents": "patent",
            "scientific_articles": "scientific_article",
        }.get(class_dir.name, "?")

        ok = "OK" if doc_type.value == expected else "FAIL"
        total += 1
        correct += int(doc_type.value == expected)

        short_path = f"{class_dir.name}/{sample.name[:32]}..."
        print(f"[{ok}] {short_path:<53} {doc_type.value:<20} {confidence:.2%}")

    print("-" * 78)
    print(f"Result: {correct}/{total} correct")


if __name__ == "__main__":
    main()
