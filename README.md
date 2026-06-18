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
