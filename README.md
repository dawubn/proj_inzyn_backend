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

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Register a new user |
| POST | `/api/v1/auth/login` | Login, get JWT tokens |
| POST | `/api/v1/auth/refresh` | Refresh access token |
| GET | `/api/v1/users/me` | Get current user profile |

### Documents
| Method | Path | Description | Access |
|--------|------|-------------|--------|
| POST | `/api/v1/documents` | Upload a document (PDF/JPG/PNG) | Owner |
| GET | `/api/v1/documents/{document_id}` | Get document details | Owner, Admin |
| DELETE | `/api/v1/documents/{document_id}` | Delete document | Owner |
| GET | `/api/v1/documents/admin/all` | List all documents (paginated) | Admin only |

### Redactions & OCR Analysis
| Method | Path | Description | Response | Access |
|--------|------|-------------|----------|--------|
| POST | `/api/v1/redactions` | Inline local OCR + redaction (returns file) | PDF/PNG file (HTTP 200) | Owner |
| POST | `/api/v1/redactions/local-ocr` | Async local OCR (Tesseract) | Analysis ID (HTTP 202) | Owner |
| POST | `/api/v1/redactions/full-ocr` | Async full OCR pipeline: local → redaction → Azure | Analysis ID (HTTP 202) | Owner |
| POST | `/api/v1/redactions/azure-ocr` | Async Azure OCR only | Analysis ID (HTTP 202) | Owner |
| POST | `/api/v1/redactions/legal-analysis` | Async full pipeline + legal analysis (LLM) | Analysis ID (HTTP 202) | Owner |
| GET | `/api/v1/redactions` | List user's analyses | Analyses list | Owner |
| GET | `/api/v1/redactions/{analysis_id}` | Get analysis status & results | Analysis details | Owner, Admin |
| DELETE | `/api/v1/redactions/{analysis_id}` | Delete analysis | HTTP 204 | Owner |
| GET | `/api/v1/redactions/admin/all` | List all analyses (all users) | Analyses list | Admin only |

### Synchronous Redaction (`POST /api/v1/redactions`)

Accepts `multipart/form-data` with field `file` (PDF, PNG, JPG/JPEG).
Runs OCR locally via Tesseract, detects sensitive data with regex + spaCy NER, and returns the
file with all detected fields masked by black rectangles. **Nothing is stored in database.**

Detected types: PESEL (11-digit), e-mail, phone (Polish mobile), numeric dates,
Polish descriptive dates (`27 stycznia 1975`), person names, places, English-style addresses.

```bash
# PDF — returns anonymized PDF
curl -X POST http://localhost:8000/api/v1/redactions \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@sample.pdf" --output anonymized.pdf

# Image — returns anonymized PNG
curl -X POST http://localhost:8000/api/v1/redactions \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@scan.jpg" --output anonymized.png
```

### Asynchronous Redaction & Analysis

All async endpoints accept either:
- **`document_id`** (query param) — existing document (reads from storage)
- **`file`** (multipart upload) — new file to process

All return `202 Accepted` with analysis ID and task ID:
```json
{
  "analysis_id": "uuid",
  "task_id": "celery-task-id",
  "message": "Pipeline queued"
}
```

Use `GET /api/v1/redactions/{analysis_id}` to poll analysis status.

#### Legal Analysis (`POST /api/v1/redactions/legal-analysis`)

Full pipeline: **local OCR → redaction → Azure OCR → LLM legal analysis**

Returns structured legal analysis (when completed):
```json
{
  "legal_analysis_result": {
    "prompt": "Legal analysis prompt (Polish)",
    "summary": "Document summary",
    "errors": [
      {
        "issue": "Missing signature",
        "text_reference": "Exact text from document",
        "severity": "critical|high|medium"
      }
    ],
    "applicable_laws": [
      {
        "law": "Ustawa o ochronie danych",
        "description": "Application description",
        "reference": "Art. 5"
      }
    ]
  },
  "processing_stage": "completed|pending|local_ocr|redaction|azure_ocr|llm_analysis",
  "processing_step": 0-4
}
```

Requires `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY` in `.env`.

#### Processing Status Tracking

All async analyses track progress:
- `processing_stage`: enum (pending → local_ocr → redaction → azure_ocr → llm_analysis → completed)
- `processing_step`: 0-4 (calculate progress: `step / 4 * 100%`)
- `status`: PENDING | IN_PROGRESS | COMPLETED | OCR_FAILED

#### Example: Track Legal Analysis

```bash
# Start analysis
ANALYSIS_ID=$(curl -X POST http://localhost:8000/api/v1/redactions/legal-analysis \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@contract.pdf" | jq -r '.analysis_id')

# Poll status
curl -X GET http://localhost:8000/api/v1/redactions/$ANALYSIS_ID \
  -H "Authorization: Bearer <TOKEN>" | jq '.processing_stage, .processing_step'
```

### Access Control

- **Non-admin users**: Can only access their own documents and analyses
- **Admin users**: Full access to all documents and analyses across all users
- **Attempting unauthorized access**: Returns 404 NotFound (not 403 for security)
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

We work in the model: main = stable code only. Every change goes through a separate branch and Pull Request.

### 1. Update local main.

Before starting a new feature:

```bash
git checkout main
git pull origin main
```

### 2. Create a branch from main.

Naming pattern: `typ/jira_task/opis-zadania`

| Type | Example |
|-----|---------|
| `feature` | `feature/PP-1/auth-ui` |
| `fix` | `fix/PP-3/login-validation` |
| `chore` | `chore/PP-4/setup-i18n` |

```bash
git checkout -b feature/PP-1/auth-ui
```

### 3. Commit changes.

Commits should follow **Conventional Commits**. Pattern: `typ:JIRA_TASK: opis`

```bash
git add .
git commit -m "feat:PP-1: add language switch"
```

Available types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `perf`

### 4. Push the branch to GitHub.

```bash
git push -u origin feature/PP-1/auth-ui
```

### 5. Open a Pull Request

On GitHub, open a PR from the branch to `main`. The PR requires a review before merging.

## Configuration

### Environment Variables

See `.env.example` for all required and optional variables.

Key variables:
- `APP_SECRET_KEY` — JWT signing key (generate: `openssl rand -hex 32`)
- `DATABASE_URL` — PostgreSQL connection string
- `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` — Azure Document Intelligence endpoint
- `AZURE_DOCUMENT_INTELLIGENCE_KEY` — Azure Document Intelligence API key
- `AZURE_OPENAI_ENDPOINT` — Azure OpenAI endpoint (for legal analysis)
- `AZURE_OPENAI_KEY` — Azure OpenAI API key (for legal analysis)

### Azure Configuration

#### Azure Document Intelligence (OCR)

1. Create resource in [Azure Portal](https://portal.azure.com)
2. Copy **Endpoint** and **Key** (from "Keys and Endpoint")
3. Add to `.env`:
   ```
   AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<region>.api.cognitive.microsoft.com/
   AZURE_DOCUMENT_INTELLIGENCE_KEY=<key>
   ```

#### Azure OpenAI (Legal Analysis)

1. Create Azure OpenAI resource in [Azure Portal](https://portal.azure.com)
2. Deploy **gpt-4** model (via "Model deployments")
3. Copy **Endpoint** and **Key** (from "Keys and Endpoint")
4. Add to `.env`:
   ```
   AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
   AZURE_OPENAI_KEY=<key>
   ```

> **Note:** Legal analysis (`POST /api/v1/redactions/legal-analysis`) gracefully degrades if Azure OpenAI is not configured — returns empty results.
