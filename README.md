# CerberDoc Backend

REST API backend for document completeness analysis and local anonymisation.

## Stack

- **Python 3.12** + **FastAPI** — async REST API
- **SQLAlchemy 2.0** + **Alembic** — ORM & migrations (PostgreSQL)
- **Celery** + **Redis** — async task queue
- **Azure AI Document Intelligence** — cloud OCR provider
- **Tesseract OCR** + **RapidOCR** + **spaCy** + **HerBERT NER** + **PyMuPDF** — local OCR and anonymisation
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

# Download anonymisation models
REDACTION_MODELS_DIR=./models python scripts/download_redaction_models.py

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

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Register a new user |
| POST | `/api/v1/auth/login` | Login, get JWT tokens |
| POST | `/api/v1/auth/refresh` | Refresh access token |
| POST | `/api/v1/auth/forgot-password` | Send a password reset link by email |
| POST | `/api/v1/auth/reset-password` | Set a new password with the token from the link |
| POST | `/api/v1/auth/verify-email` | Confirm the email with the token from the link |
| POST | `/api/v1/auth/resend-verification` | Send a new email verification link |
| GET | `/api/v1/users/me` | Get current user profile |
| PATCH | `/api/v1/users/me` | Update own name, surname and company |
| POST | `/api/v1/users/me/password` | Change own password (requires the current one) |

#### Password reset

`forgot-password` always answers `202` with the same message, whether or not the email is
registered. When it is, the user gets a link to `FRONTEND_URL/reset-password?token=...`.
The link is valid for `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` (30 by default), works once,
and a newer link replaces the older one. Setting a new password ends all existing sessions
of the user.

One account gets at most one email a minute (`PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS`) and at
most `PASSWORD_RESET_MAX_PER_DAY` (5) within 24 hours; further requests get the same `202`
answer but no email.

#### Email verification (production)

With `EMAIL_VERIFICATION_REQUIRED=true` (set in `docker-compose.prod.yml`, off by default)
a new account gets a link to `FRONTEND_URL/verify-email?token=...` and cannot log in until
it is opened. Login then answers `403` with `"code": "email_not_verified"`, and
`resend-verification` sends a new link (same `202` answer for every email). The link is valid
for `EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS` (24 by default) and works once. One account gets
at most one email a minute and `EMAIL_VERIFICATION_MAX_PER_DAY` (5, the registration email
included) within 24 hours. Completing a
password reset also confirms the email. Accounts that existed before this feature are
marked as verified by the migration. Without the setting, new accounts are verified at once.

#### Request limits per IP

The public auth endpoints accept a limited number of requests from one IP address;
over the limit they answer `429` with `"code": "too_many_requests"` and a `Retry-After`
header. The counters are kept in Redis (`REDIS_URL`), so all API workers share them.

| Endpoint | Limit per IP |
|----------|--------------|
| `login` | 10 a minute |
| `register` | 5 an hour |
| `forgot-password`, `resend-verification` | 10 an hour |
| `reset-password`, `verify-email` | 20 an hour |
| `contact` | 3 a day (`CONTACT_MAX_PER_IP_PER_DAY`) |

The limits are set in `app/api/rate_limit.py` and turned off with `RATE_LIMIT_ENABLED=false`.
When Redis does not answer, requests are let through and a warning is logged.

Behind a reverse proxy the API sees the proxy's address, and everyone would share one limit.
The client address is found in two steps:

1. uvicorn takes the address from `X-Forwarded-For` only for proxies listed in
   `FORWARDED_ALLOW_IPS`. `docker-compose.prod.yml` trusts the private networks, where the
   Coolify proxy runs (the API is only `expose`d, so nobody else connects from them). Do not
   use `*`: uvicorn then takes the first address of the header, which the client can fake.
2. When that address belongs to Cloudflare (`CLOUDFLARE_IP_RANGES`, by default the list from
   https://www.cloudflare.com/ips/), the client address is read from `CF-Connecting-IP`.
   From anyone else the header is ignored, so it cannot be faked by connecting to the server
   directly. Update the list if Cloudflare publishes new ranges.

IPv6 addresses are counted per `/64` network, which one connection usually gets as a whole.
After a deployment, the `Rate limit exceeded` warnings in the `api` log should show addresses
of users, not of Cloudflare or of the proxy.

Emails are sent over SMTP (`SMTP_*` variables in `.env`). Without `SMTP_HOST` nothing is
sent; in development the message with the link is written to the API log, so the flow can
be tested without a mail server.

### Contact form
| Method | Path | Description | Access |
|--------|------|-------------|--------|
| POST | `/api/v1/contact` | Send a message to the team (`202`) | Public |

The message is emailed to `CONTACT_EMAIL` with `Reply-To` set to the sender, so the team
answers from its mailbox. The address is kept only in the server configuration and never
reaches the browser; without it the endpoint answers `503` (`"code": "contact_unavailable"`).
No confirmation goes to the address typed in the form, so the form cannot be used to send
emails to other people.

Protection against spam and abuse:

- `CONTACT_MAX_PER_IP_PER_DAY` (3) messages a day per IP and `CONTACT_MAX_PER_DAY` (20) a day
  from everyone together (`429` with `Retry-After`; days are counted in UTC)
- a hidden `website` field: when a bot fills it, the answer is `202` but nothing is sent
- name and subject must be a single line (no line breaks that could add email headers)
- control and invisible characters (e.g. right-to-left override) are removed; the
  message must have 10 to 5000 characters and at most 3 links; unknown fields are rejected
- the email is plain text and says that the sender's address has not been verified

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
| GET | `/api/v1/redactions/options` | What can be configured when an analysis is started | Categories and AI models | Any user |
| GET | `/api/v1/redactions` | List user's analyses | Analyses list | Owner |
| GET | `/api/v1/redactions/{analysis_id}` | Get analysis status & results | Analysis details | Owner, Admin |
| DELETE | `/api/v1/redactions/{analysis_id}` | Delete analysis | HTTP 204 | Owner |
| GET | `/api/v1/redactions/admin/all` | List all analyses (all users) | Analyses list | Admin only |

### Synchronous Redaction (`POST /api/v1/redactions`)

Accepts `multipart/form-data` with field `file` (PDF, PNG, JPG/JPEG).
Runs OCR locally via Tesseract and RapidOCR, detects sensitive data with rules, NER and image checks,
and returns the file with all detected fields masked by black rectangles. **Nothing is stored in database.**

Detected types: PESEL, NIP, REGON, KRS, ID and passport numbers, bank accounts, cards, phones,
e-mails, person names, addresses, signatures, faces and QR or bar codes. Dates stay visible.

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

#### Analysis options

`POST /api/v1/redactions/legal-analysis` accepts optional query parameters that change
how a single analysis runs. Without them the full pipeline runs.

| Parameter | Default | Effect |
|-----------|---------|--------|
| `anonymize` | `true` | `false` sends the document on without masking personal data |
| `redaction_categories` | all | Kinds of data to mask (repeatable): `person`, `address`, `date`, `pesel`, `email`, `phone` |
| `ai_analysis` | `true` | `false` skips the legal analysis by the AI model |
| `ai_model` | `AZURE_OPENAI_DEFAULT_MODEL` | One of `AZURE_OPENAI_MODELS` |

`AZURE_OPENAI_MODELS` lists the Azure OpenAI deployments a user can choose from; the names
have to match deployments that exist in the Azure OpenAI resource. The options used are
returned with the analysis in the `options` field.

`GET /api/v1/redactions/options` describes each model with its relative cost, speed and
quality and with the result of a comparison on a test document. These profiles live in
`app/core/ai_models.py`; a deployment without a profile is offered without a description.
Reasoning models (`gpt-6*`) accept only the default temperature, so the call leaves it out
for them, and retries without it when another deployment rejects it.

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

Every account is created with the `user` role; the role cannot be chosen at registration.
To make an existing account an admin:

```bash
docker compose exec api python scripts/set_user_role.py user@example.com admin
```

Docker handles all OCR dependencies automatically during `docker compose up --build`.

## Anonymisation

Pages are masked locally before anything is sent to Azure. Detection lives in
`app/services/redaction_*.py`.

Polish NER uses [lexedit/herbert-polish-legal-ner-ocr](https://huggingface.co/lexedit/herbert-polish-legal-ner-ocr)
(CC BY 4.0), face detection uses YuNet from the OpenCV Zoo (MIT).

## Document Classification

`POST /api/v1/classify` accepts OCR text and returns a predefined document type with confidence.
Uses a TF-IDF + Logistic Regression pipeline trained on generated training text. The endpoint
accepts the normalised payload (`text_content`), Azure-style alias (`content`), or the full raw
OCR payload (`ocr_raw_result.content`).

Detected types: `contract`, `invoice`, `patent`, `lawsuit`, `power_of_attorney`,
`application` (job application or CV) and `demand_letter` (pre-trial demand for payment).
Anything else is returned as `unknown`.

The same classifier runs automatically after every OCR analysis — its result is stored on the
analysis (`detected_document_type`, `classification_confidence`) and suggested on the document
(`suggested_document_type`), so the frontend gets the type without an extra call.

```bash
curl -X POST http://localhost:8000/api/v1/classify \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"content":"Patent claim invention embodiment prior art"}'
```

Train (or retrain) the model with:

```bash
# 1. Generate synthetic training data (creates storage/classifier_training/)
python scripts/generate_synthetic_training_data.py --train 200 --valid 60

# 2. Train the model (writes storage/classifier_model.joblib)
python scripts/train_classifier.py
```

Training data lives under `storage/classifier_training/{train,valid}/<class_name>/*.txt`
(folder name = class label). Both the training files and the model are **gitignored** —
they are generated automatically and do not need to be committed to the repository.

Model output path is configurable via the `CLASSIFIER_MODEL_PATH` env variable.

> **Synthetic vs real data:** The generator produces vocabulary-template samples as a
> "cold start". Replace generated `.txt` files with real OCR output to improve
> real-world accuracy.

The classifier model is trained automatically during `docker compose up --build`
(the Dockerfile runs `generate_synthetic_training_data.py` then `train_classifier.py`),
so a freshly built image ships with a ready-to-use `classifier_model.joblib`. After
retraining locally, restart the API and worker so they pick the new file up:

```bash
docker compose restart api worker
```

Predictions with a probability below `CLASSIFIER_MIN_CONFIDENCE` (default `0.45`)
are returned as `DocumentType.UNKNOWN` so the frontend can ask the user to
confirm the type instead of trusting a weak guess.

### Adding new document types

To add a new document type:

1. Add the value to `DocumentType` in `app/enums/document.py` (e.g. `DEED = "deed"`) and a
   migration that adds it to the `ck_documents_document_type` constraint.
2. Add a validation profile: a spec in `app/services/formal_catalog.py` or a built-in profile in
   `app/services/mvp_validation_profiles.py`.
3. Add a template entry for the new class in `scripts/generate_synthetic_training_data.py`
   (`_CLASS_TEMPLATES` dict — folder name = class label, e.g. `"deeds": {...}`).
4. Map the folder name to the enum value in `_LABEL_TO_DOCUMENT_TYPE` in
   `app/services/classification.py` (e.g. `"deeds": DocumentType.DEED`).
5. Regenerate training data and retrain the model:
   ```bash
   python scripts/generate_synthetic_training_data.py
   python scripts/train_classifier.py
   ```
6. Restart the API and worker so they pick up the new model:
   ```bash
   docker compose restart api worker
   ```

Labels that exist in the model but are not mapped fall back to `DocumentType.UNKNOWN`.

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
- `REDACTION_MODELS_DIR` — local anonymisation models (default `/opt/cerberdoc/models`)
- `REDACTION_SECOND_OCR_ENABLED`, `REDACTION_VERIFY_ENABLED` — second OCR engine and re-check of masked pages

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

## Effectiveness Tests

Generates 100 synthetic PDFs across 7 document types and runs the backend pipeline. Measures missing-field detection (TP, FN, FP, recall, precision), invalid-date outcomes, report consistency, and PDF export availability.

Grammar defects, advisory fields, signatures, and visual elements are not scored. Classification accuracy is not tested.

### Running

From the backend directory:

```powershell
py -3.12 -m tests.effectiveness.run
```

Generate documents only:

```powershell
py -3.12 -m tests.effectiveness.run --generate-only
```

Requires a running backend and a test account. Credentials are prompted or read from `TEST_ACCESS_TOKEN`, `TEST_EMAIL`, and `TEST_PASSWORD`. Real OCR/LLM calls may incur charges.

### Results

Saved in `tests/effectiveness/result/test-YYYY-MM-DD/`:

- `documents/` — generated PDFs.
- `reports/` — downloaded PDF reports.
- `log.json` — execution log.
- `report_result.json` — detailed results.
- `chart.png` — detection metrics and report consistency.

Report consistency requires independent summaries via `--reference-summaries`. Without them, reports remain unchecked and execution returns a non-zero exit code. PDF checks verify availability and the header, not full content correctness.
