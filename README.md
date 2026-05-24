# CerberDoc Backend

REST API backend for document completeness analysis using Azure AI Document Intelligence (OCR).

## Stack

- **Python 3.12** + **FastAPI** — async REST API
- **SQLAlchemy 2.0** + **Alembic** — ORM & migrations (PostgreSQL)
- **Celery** + **Redis** — async task queue
- **Azure AI Document Intelligence** — OCR provider
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

The API will be available at http://localhost:8000
Interactive docs: http://localhost:8000/docs
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

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

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
| POST | `/api/v1/redactions` | Upload a document and receive a redacted copy |
| POST | `/api/v1/documents/{id}/analyses` | Trigger OCR analysis |
| GET | `/api/v1/documents/{id}/analyses/{aid}` | Get analysis status & results |
| POST | `/api/v1/validation-profiles` | Create validation profile (admin) |
| GET | `/api/v1/validation-profiles` | List validation profiles (admin) |
| GET | `/api/v1/reports/{analysis_id}` | Get analysis report |

## Project Structure

```
app/
├── api/           # FastAPI routers & dependencies
├── adapters/      # External service adapters (Azure OCR)
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

## Sensitive Data Redaction

The `POST /api/v1/redactions` endpoint accepts an authenticated multipart upload and
returns a redacted copy of the uploaded file. The file is processed in temporary storage
only; neither the original nor the redacted output is saved in the database or application
storage.

Supported input formats follow the standard upload configuration: PDF, JPG, JPEG and PNG.
PDF output is returned as PDF, and image output keeps the source image format.

Example:

```bash
curl -X POST http://localhost:8000/api/v1/redactions \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@document.pdf" \
  -o document_redacted.pdf
```

The response body is the redacted file. Summary metadata is returned in HTTP headers:

- `X-Redaction-Findings-Count`
- `X-Redaction-Boxes-Count`
- `X-Redaction-Pages`
- `X-Redaction-Types`

Supported sensitive data types in v1: PESEL, NIP/VAT, REGON, IBAN, e-mail address,
phone number, date, postal code, Polish ID card number, passport number, contextual
person names, organization/company names, addresses, contact fields and signature areas.
Redacted regions are flattened into the returned PDF/image and labelled with the
detected data type.

The redaction rules are regex-first. Besides exact regex matches, the service redacts a
bounded number of value words after common Polish and international labels such as
`Name`, `Full Name`, `Address`, `Adresse`, `Company`, `Organization`, `Email`, `Phone`,
`VAT` and `Signature`. It also masks common first-name + surname combinations and
company names with suffixes such as `Sp. z o.o.`, `S.A.`, `Ltd`, `LLC`, `GmbH`, `Inc`
and `Corp`.

Limitations: detection is based on OCR plus deterministic regular expressions. It does
not use machine-learning NER in v1, so unusual free-form personal data can still be
missed, while malformed OCR text can occasionally create false positives. OCR quality
depends on scan resolution, document rotation, language data available to Tesseract and
the readability of the source file.
