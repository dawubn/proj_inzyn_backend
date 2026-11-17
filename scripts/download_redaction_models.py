"""Downloads anonymization models into REDACTION_MODELS_DIR, run while building the image."""

import os
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rapidocr import RapidOCR

MODELS_DIR = Path(os.environ.get("REDACTION_MODELS_DIR", "/opt/cerberdoc/models"))

# Pinned revisions, so a rebuilt image reads documents exactly like the tested one.
HERBERT_REPO = "lexedit/herbert-polish-legal-ner-ocr"
HERBERT_REVISION = "a4b856cfa43595172d2137c815ef8a81d0152259"
HERBERT_FILES = ("config.json", "tokenizer.json", "onnx/model_quantized.onnx")
YUNET_REPO = "opencv/face_detection_yunet"
YUNET_REVISION = "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"
YUNET_FILE = "face_detection_yunet_2023mar.onnx"


def _fetch(repo: str, revision: str, name: str, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
    response = httpx.get(url, follow_redirects=True, timeout=120)
    response.raise_for_status()
    target.write_bytes(response.content)
    print(f"  {target}")


def _rapidocr() -> None:
    engine = RapidOCR(
        params={
            "Global.log_level": "error",
            "Global.model_root_dir": str(MODELS_DIR / "rapidocr"),
            "Det.lang_type": "pl",
            "Rec.lang_type": "pl",
        }
    )
    # Recognition models load only when some text is found, so give it a line.
    image = Image.new("RGB", (640, 120), "white")
    ImageDraw.Draw(image).text(
        (20, 30), "Zażółć 123", fill="black", font=ImageFont.load_default(48)
    )
    engine(np.array(image))
    print(f"  {MODELS_DIR / 'rapidocr'}")


def main() -> None:
    print(f"Downloading redaction models to {MODELS_DIR}")
    for name in HERBERT_FILES:
        target = MODELS_DIR / HERBERT_REPO.split("/")[1] / name
        _fetch(HERBERT_REPO, HERBERT_REVISION, name, target)
    _fetch(YUNET_REPO, YUNET_REVISION, YUNET_FILE, MODELS_DIR / YUNET_FILE)
    _rapidocr()


if __name__ == "__main__":
    main()
