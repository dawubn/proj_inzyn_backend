# Document Analyzer Backend

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
git clone <repo-url> && cd doc-analyzer-backend
cp .env.example .env
# Edit .env — set APP_SECRET_KEY, AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT, AZURE_DOCUMENT_INTELLIGENCE_KEY
```

### 3. Start services

```bash
docker compose up --build
```

The API will be available at http://localhost:8000
Interactive docs: http://localhost:8000/docs
Celery Flower: http://localhost:5555

### 4. Run migrations

```bash
docker compose exec api alembic upgrade head
```

### 5. Health check

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

## Coding Conventions

See `CONVENTIONS.md` for team coding guidelines.

## Environment Variables

See `.env.example` for all required and optional variables.
