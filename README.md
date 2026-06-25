# CerberDoc Backend

REST API backend for document completeness analysis and local anonymisation.

## Stack

- **Python 3.12** + **FastAPI** — async REST API
- **SQLAlchemy 2.0** + **Alembic** — ORM & migrations (PostgreSQL)
- **Celery** + **Redis** — async task queue
- **Azure AI Document Intelligence** — cloud OCR provider
- **Tesseract OCR** + **spaCy** + **PyMuPDF** — local OCR and anonymisation
- **JWT** — authentication
- **Docker Compose** — local development

## Quick Start

### 1. Prerequisites

- Docker & Docker Compose
- Python 3.12 (for local dev without Docker)

### 2. Clone & configure

```bash
git clone <repo-url> && cd proj_inzyn_backend
cp .env.example .env
# Edit .env — set APP_SECRET_KEY, AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT, AZURE_DOCUMENT_INTELLIGENCE_KEY
```

### 3. Install pre-commit hooks (jednorazowo, każdy developer)

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

Od tej chwili przy każdym `git commit` automatycznie uruchamia się lint, formatowanie i sprawdzanie formatu wiadomości commita.

### 4. Start services

```bash
docker compose up --build
```

> **Note:** The first build downloads spaCy language models (~1 GB) and takes 10–15 minutes.
> Subsequent builds use Docker layer cache and are fast.

The API will be available at http://localhost:8000
Interactive docs (DEBUG mode): http://localhost:8000/docs
Celery Flower: http://localhost:5555

### 5. Run migrations

```bash
docker compose exec api alembic upgrade head
```

### 6. Health check

```bash
curl http://localhost:8000/health
```

## Local Development (without Docker)

Additional dependencies for local OCR/redaction:

The commands below are only examples. The project does not require any specific package manager or installation method.

MacOS with Homebrew:
```bash
brew install tesseract tesseract-lang
```

MacOS with Conda:
```bash
conda install -c conda-forge tesseract
```

Debian/Ubuntu:
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng
```

Windows with winget:
```powershell
winget install UB-Mannheim.TesseractOCR
```

Verify installation:
```bash
tesseract --version
```

Docker installs these dependencies automatically. They are required only when running the app locally without Docker.

Then set up the project:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Download spaCy language models (~1 GB, one-time)
python -m spacy download pl_core_news_lg
python -m spacy download en_core_web_lg

# Start DB + Redis separately (e.g. via Docker)
docker compose up db redis -d

# Copy and edit env
cp .env.example .env

# Run migrations
alembic upgrade head

# Train the document classifier (writes storage/classifier_model.joblib)
python scripts/train_classifier.py

# Start API
uvicorn main:app --reload

# Start Celery worker (separate terminal)
celery -A app.tasks.celery_app worker --loglevel=info
```

## Running Tests

```bash
# Requires a running PostgreSQL test database
pytest
```

To run a single test file:

```bash
pytest tests/api/test_auth.py -v
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Register a new user |
| POST | `/api/v1/auth/login` | Login, get JWT tokens |
| POST | `/api/v1/auth/refresh` | Refresh access token |
| GET | `/api/v1/users/me` | Get current user profile |
| POST | `/api/v1/documents` | Upload a document (PDF/JPG/PNG) |
| GET | `/api/v1/documents` | List your documents (paginated) |
| GET | `/api/v1/documents/{id}` | Get document details |
| POST | `/api/v1/documents/{id}/analyses` | Trigger OCR analysis |
| GET | `/api/v1/documents/{id}/analyses/{aid}` | Get analysis status & results |
| POST | `/api/v1/validation-profiles` | Create validation profile (admin) |
| GET | `/api/v1/validation-profiles` | List validation profiles (admin) |
| GET | `/api/v1/reports/{analysis_id}` | Get analysis report |
| POST | `/api/v1/classify` | Classify document type from Azure OCR payload |
| POST | `/api/v1/redactions` | Locally anonymise a document — PDF or image in, masked file out |

## Local Redaction

`POST /api/v1/redactions` accepts `multipart/form-data` with field `file` (PDF, PNG, JPG/JPEG).
Runs OCR locally via Tesseract, detects sensitive data with regex + spaCy NER, and returns the
file with all detected fields masked by black rectangles. Nothing is stored in the database.

Detected types: PESEL (any 11-digit sequence), e-mail, phone (Polish mobile), numeric dates,
Polish descriptive dates (`27 stycznia 1975`), person names, places, English-style addresses.

```bash
# PDF
curl -X POST http://localhost:8000/api/v1/redactions \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@sample.pdf" --output anonymized_sample.pdf

# Image
curl -X POST http://localhost:8000/api/v1/redactions \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@scan.jpg" --output anonymized_scan.png
```

Docker handles all OCR dependencies automatically during `docker compose up --build`.

## Document Classification

`POST /api/v1/classify` accepts OCR text and returns a predefined document type with confidence.
Uses a TF-IDF + Logistic Regression pipeline trained on the DocLayNet dataset. The endpoint
accepts the normalised payload (`text_content`), Azure-style alias (`content`), or the full raw
OCR payload (`ocr_raw_result.content`).

Detected types: `financial_report`, `government_tender`, `law_and_regulation`, `manual`,
`patent`, `scientific_article`, `invoice`, `contract`, `id_card`, `passport`,
`bank_statement`, `tax_form`.

The same classifier runs automatically after every OCR analysis — its result is stored on the
analysis (`detected_document_type`, `classification_confidence`) and on the document
(`document_type`), so the frontend gets the type without an extra call.

```bash
curl -X POST http://localhost:8000/api/v1/classify \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"content":"Patent claim invention embodiment prior art"}'
```

Train (or retrain) the model with:

```bash
python scripts/train_classifier.py
```

Training data lives under `storage/classifier_training/{train,valid}/<class_name>/*.txt`
(folder name = class label). The class folders are checked in empty — see
`storage/classifier_training/README.md` for the format and how to populate them.
Model output path is configurable via the `CLASSIFIER_MODEL_PATH` env variable.

For classes without a public corpus (invoices, contracts, ID/passport, bank
statements, tax forms) the repo ships a synthetic data generator that produces
text samples from realistic vocabulary templates:

```bash
python scripts/generate_synthetic_training_data.py --train 200 --valid 60
```

Synthetic data is a "cold start" — replace it with real OCR text whenever
available to improve real-world accuracy.

The classifier model is trained automatically during `docker compose up --build`
(see the `RUN python scripts/train_classifier.py` step in `Dockerfile`), so a
freshly built image ships with a ready-to-use `classifier_model.joblib`. After
retraining locally, restart the API and worker so they pick the new file up:

```bash
docker compose restart api worker
```

Predictions with a probability below `CLASSIFIER_MIN_CONFIDENCE` (default `0.45`)
are returned as `DocumentType.UNKNOWN` so the frontend can ask the user to
confirm the type instead of trusting a weak guess.

### Adding new document types

The classifier picks up new classes automatically from the training dataset — no code change in
the endpoint or schemas is required. To add a new type:

1. Add the value to `DocumentType` in `app/enums/document.py` (e.g. `INVOICE = "invoice"`).
2. Collect labelled OCR text (one `.txt` per document) in the following layout — folder name = class label:
   ```
   storage/classifier_training/
   ├── train/
   │   ├── invoices/         # ~200+ samples per class recommended
   │   ├── contracts/
   │   └── ...
   └── valid/
       ├── invoices/         # ~60+ samples per class
       ├── contracts/
       └── ...
   ```
3. Map the folder name to the enum value in `_LABEL_TO_DOCUMENT_TYPE` in
   `app/services/classification.py` (e.g. `"invoices": DocumentType.INVOICE`).
4. Retrain the model:
   ```bash
   python scripts/train_classifier.py
   ```
5. Restart the API container so the new model is loaded:
   ```bash
   docker compose restart api worker
   ```

Labels that exist in the model but are not mapped fall back to `DocumentType.OTHER`.

## Project Structure

```
app/
├── api/           # FastAPI routers & dependencies
├── adapters/      # External service adapters (Azure OCR, local Tesseract OCR)
├── core/          # Config, security, exceptions, logging
├── db/            # DB engine, session, model registration
├── enums/         # StrEnum definitions
├── models/        # SQLAlchemy ORM models
├── repositories/  # Data access layer
├── schemas/       # Pydantic v2 schemas
├── services/      # Business logic layer
└── tasks/         # Celery tasks
```

## Git Workflow

Pracujemy w modelu: **`main` = tylko stabilny kod**. Każda zmiana idzie przez osobny branch i Pull Request.

### 1. Zaktualizuj main lokalnie

Zanim zaczniesz nową funkcję:

```bash
git checkout main
git pull origin main
```

### 2. Stwórz branch od main

Wzór nazwy: `typ/jira_task/opis-zadania`

| Typ | Przykład |
|-----|---------|
| `feature` | `feature/PP-1/auth-ui` |
| `fix` | `fix/PP-3/login-validation` |
| `chore` | `chore/PP-4/setup-i18n` |

```bash
git checkout -b feature/PP-1/auth-ui
```

### 3. Commituj zmiany

Commity zgodne z **Conventional Commits**. Wzór: `typ:JIRA_TASK: opis`

```bash
git add .
git commit -m "feat:PP-1: add language switch"
```

Dostępne typy: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `perf`

### 4. Wypchnij branch na GitHuba

```bash
git push -u origin feature/PP-1/auth-ui
```

### 5. Otwórz Pull Request

Na GitHubie otwórz PR z brancha do `main`. PR wymaga review przed mergem.

## Environment Variables

See `.env.example` for all required and optional variables.
