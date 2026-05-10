# Receiptory v1 — Design Spec

Self-hosted receipt, invoice, and document management system for self-employed professionals. This spec covers the first sub-project: web upload, LLM processing, document browser, export, and backup.

## Scope

**In scope:** Web UI upload, LLM extraction pipeline, document browser with search/filter/edit, export (zip + CSV), backup to cloud, admin settings, category management, single-user auth.

**Deferred to later sub-projects:** Telegram bot, Gmail adapter, watched folder, currency conversion, clarification workflow, near-duplicate detection, vendor normalization.

---

## Architecture

**Stack:** Python (uv), FastAPI, React (Vite + shadcn/ui), SQLite (WAL + FTS5), Docker Compose.

**Approach:** Monolith — single FastAPI process. The processing queue and backup scheduler run as background asyncio tasks started via FastAPI's lifespan. One Docker container. FastAPI serves both the API and the built frontend static files in production.

**LLM:** litellm abstraction layer, defaulting to `gemini/gemini-3-flash-preview`. Single-pass extraction (OCR + fields + classification + confidence in one prompt).

---

## Project Structure

```
receiptory/
├── backend/
│   ├── main.py                  # FastAPI app, lifespan (starts queue + backup scheduler)
│   ├── config.py                # Settings loader (env > db > defaults)
│   ├── auth.py                  # Single-user session auth (httponly cookie)
│   ├── database.py              # SQLite connection, WAL mode, migration runner
│   ├── api/
│   │   ├── documents.py         # CRUD, search, filter, edit
│   │   ├── upload.py            # File upload with dedup
│   │   ├── export.py            # Export zip generation
│   │   ├── settings.py          # Admin settings
│   │   ├── categories.py        # Category management
│   │   ├── backup.py            # Backup trigger, history, download, delete
│   │   ├── queue.py             # Queue status
│   │   ├── stats.py             # Dashboard stats, processing costs
│   │   ├── auth.py              # Login/session endpoints
│   │   └── logs.py              # Log viewer
│   ├── processing/
│   │   ├── queue.py             # Background loop: poll DB, process one at a time
│   │   ├── pipeline.py          # Orchestrator: normalize -> extract -> file
│   │   ├── normalize.py         # Image->PDF (Pillow), HTML->PDF (weasyprint)
│   │   ├── extract.py           # LLM call via litellm, prompt, response parsing
│   │   └── filing.py            # Generate stored filename, move to storage
│   ├── backup/
│   │   ├── scheduler.py         # Cron-based background task
│   │   ├── runner.py            # Build backup (files, DB, JSONL, settings, logs)
│   │   └── rclone.py            # rclone wrapper for cloud upload + retention
│   ├── storage.py               # File I/O: save, serve, page rendering + cache
│   └── models.py                # Pydantic models for API request/response
├── frontend/                    # Vite + React + shadcn/ui
├── migrations/
│   ├── 001_initial_schema.sql
│   └── ...
├── test_documents/              # Sample fixtures
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

---

## Database Schema

SQLite in WAL mode. Hand-rolled versioned migrations (numbered SQL files in `migrations/`, `schema_version` table tracks applied versions, runner applies unapplied files on startup).

### `schema_version`

| Column | Type | Description |
|---|---|---|
| `version` | INTEGER PK | Migration number |
| `applied_at` | TEXT | ISO timestamp |

### `settings`

| Column | Type | Description |
|---|---|---|
| `key` | TEXT PK | Setting identifier |
| `value` | TEXT | JSON-encoded value |
| `updated_at` | TEXT | Last modified |

### `categories`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT UNIQUE NOT NULL | Category name |
| `description` | TEXT | Description included in LLM prompt to guide classification |
| `is_system` | BOOLEAN DEFAULT FALSE | System categories cannot be deleted |
| `is_deleted` | BOOLEAN DEFAULT FALSE | Soft delete — hidden from classification and UI dropdowns, existing FKs preserved |
| `display_order` | INTEGER | UI ordering |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

System categories (seeded on first migration): `pending`, `not_a_receipt`, `failed`.

Starter user categories (seeded): `office_supplies`, `travel`, `meals`, `utilities`, `other`.

### `documents`

| Column | Type | Description |
|---|---|---|
| **Identity** | | |
| `id` | INTEGER PK | Auto-increment |
| `document_type` | TEXT | `expense_receipt`, `issued_invoice`, `other_document` |
| `original_filename` | TEXT | Filename as submitted |
| `stored_filename` | TEXT | `yyyy-mm-dd-vendor_receipt_id-hash.pdf` |
| `file_hash` | TEXT UNIQUE | SHA-256 of original file (dedup key) |
| `file_size_bytes` | INTEGER | |
| `page_count` | INTEGER | |
| **Ingestion** | | |
| `submission_date` | TEXT | ISO timestamp |
| `submission_channel` | TEXT | `web_upload` (others added in later sub-projects) |
| `sender_identifier` | TEXT | Null for web upload |
| **Extracted fields** | | |
| `receipt_date` | TEXT | Date on the document |
| `document_title` | TEXT | Title as it appears on the document |
| `vendor_name` | TEXT | Vendor/issuer name |
| `vendor_tax_id` | TEXT | Business number |
| `vendor_receipt_id` | TEXT | Receipt/invoice number |
| `client_name` | TEXT | Client/buyer name |
| `client_tax_id` | TEXT | Client/buyer tax ID |
| `description` | TEXT | Brief summary |
| `line_items` | TEXT | JSON array: `[{"description", "quantity", "unit_price"}]` |
| `subtotal` | REAL | Pre-tax amount |
| `tax_amount` | REAL | Tax amount |
| `total_amount` | REAL | Total amount |
| `currency` | TEXT | ISO 4217 code |
| `converted_amount` | REAL | Reserved for future currency conversion |
| `conversion_rate` | REAL | Reserved for future currency conversion |
| `payment_method` | TEXT | Cash, credit card, etc. |
| `payment_identifier` | TEXT | Card last digits, account number |
| `language` | TEXT | Detected language code |
| `additional_fields` | TEXT | JSON array: `[{"key", "value"}]` |
| `raw_extracted_text` | TEXT | Full OCR text |
| **Classification** | | |
| `category_id` | INTEGER FK | References `categories.id` |
| `status` | TEXT | `pending`, `processing`, `processed`, `failed`, `needs_review` |
| **Processing** | | |
| `extraction_confidence` | REAL | 0-1 score |
| `processing_model` | TEXT | Model used |
| `processing_tokens_in` | INTEGER | Approximate input tokens |
| `processing_tokens_out` | INTEGER | Approximate output tokens |
| `processing_cost_usd` | REAL | Estimated cost |
| `processing_date` | TEXT | When extraction ran |
| `processing_attempts` | INTEGER DEFAULT 0 | |
| `processing_error` | TEXT | Error message if failed |
| **Overrides** | | |
| `manually_edited` | BOOLEAN DEFAULT FALSE | |
| `is_deleted` | BOOLEAN DEFAULT FALSE | Soft delete |
| `edit_history` | TEXT | JSON array: `[{"field", "old_value", "new_value", "timestamp"}]` |
| `user_notes` | TEXT | |
| **Export** | | |
| `last_exported_date` | TEXT | |
| **Housekeeping** | | |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

### `documents_fts` (FTS5 virtual table)

Indexed columns: `raw_extracted_text`, `vendor_name`, `description`, `document_title`.

### `backups`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `started_at` | TEXT | When backup started |
| `completed_at` | TEXT | When backup finished |
| `status` | TEXT | `running`, `completed`, `failed` |
| `size_bytes` | INTEGER | Total backup size |
| `destination` | TEXT | rclone path used |
| `error` | TEXT | Error message if failed |
| `backup_type` | TEXT | `manual`, `daily`, `weekly`, `monthly`, `quarterly` |

---

## Processing Pipeline

Background asyncio task started via FastAPI lifespan. Processes documents sequentially from the queue.

### Queue loop (`processing/queue.py`)

1. On startup, reset any documents stuck in `processing` back to `pending` (crash recovery).
2. Poll: `SELECT` oldest document with `status = 'pending'`.
3. Set status to `processing`.
4. Run pipeline. On success: status = `processed`. On failure: status = `failed`, record error, increment `processing_attempts`.
5. Sleep for `llm_sleep_interval` seconds.
6. Repeat.

### Pipeline steps (`processing/pipeline.py`)

**1. Normalize** (`normalize.py`)
- Image files (JPEG, PNG, TIFF, HEIC) -> PDF via Pillow
- HTML files -> PDF via weasyprint
- PDF files: no conversion
- Store original in `data/storage/originals/{hash}.{ext}`
- Store converted PDF in `data/storage/converted/{hash}.pdf` (if conversion was needed)
- Count pages via PyMuPDF

**2. Render pages**
- PyMuPDF renders each page as PNG at configurable DPI (default 200)
- Images held in memory for the LLM call, not persisted at this stage

**3. Extract & classify** (`extract.py`) — single LLM pass
- Send page images + structured prompt to litellm
- Prompt context includes:
  - User's business names (multi-language array)
  - User's business addresses (multi-language array)
  - User's business tax IDs
  - Active category list with descriptions (excluding soft-deleted)
  - Instructions to return JSON matching the document schema
- LLM returns: all extracted fields, document type, category, confidence, OCR text
- Response validated against Pydantic model
- If JSON parsing fails: mark as `failed`

**4. Document type verification**
- LLM classifies document type in the prompt, but code verifies: if `vendor_tax_id` matches any of the user's `business_tax_ids` -> `issued_invoice`
- Otherwise trust LLM classification (`expense_receipt` or `other_document`)

**5. Confidence check**
- If `extraction_confidence` < `confidence_threshold` setting: status = `needs_review` instead of `processed`

**6. Filing** (`filing.py`)
- Generate stored filename: `yyyy-mm-dd-vendor_receipt_id-hash.pdf`
- `0000-00-00` if no date, `000000` if no receipt ID
- Copy the PDF to `data/storage/filed/`

**7. Database update**
- Write all extracted fields
- Set status (`processed`, `needs_review`, or `failed`)
- Record processing metadata (model, tokens, cost, timestamp)
- Insert into FTS5 index

---

## Backup System

### Backup contents
- All document files (`data/storage/`)
- SQLite database (`data/receiptory.db`)
- JSONL export of all document metadata (human-readable, app-independent)
- System settings (JSON export from settings table)
- Log files (`data/logs/`)

### Scheduler (`backup/scheduler.py`)
- Background asyncio task evaluates the cron expression from `backup_schedule` setting
- Determines backup type based on date: quarterly (Jan/Apr/Jul/Oct 1st) > monthly (1st) > weekly (Sunday) > daily
- Creates a `backups` record, runs the backup, updates status

### Runner (`backup/runner.py`)
- Assembles backup contents into a temporary directory
- JSONL export: one line per document with all metadata fields
- Settings export: all settings as JSON (API keys included — backup is encrypted at rest via rclone target)

### Cloud upload (`backup/rclone.py`)
- Shells out to `rclone` to upload to configured destination
- Applies retention policy: deletes old backups beyond configured retention windows
- rclone must be installed in the Docker container

### Retention defaults
- Daily: 7 days
- Weekly (Sunday): 4 weeks
- Monthly (1st): 3 months
- Quarterly (Jan 1, Apr 1, Jul 1, Oct 1): permanent

---

## API Endpoints

All under `/api/`, all require auth (httponly session cookie) except `/api/auth/login`.

### Auth
- `POST /api/auth/login` — validate credentials, set session cookie
- `GET /api/auth/me` — check session validity

### Upload
- `POST /api/upload` — multipart file(s), computes SHA-256, rejects duplicates, creates document with `status=pending`

### Documents
- `GET /api/documents` — list/search/filter. Params: `status`, `category_id`, `document_type`, `date_from`, `date_to`, `channel`, `search` (FTS5), `sort_by`, `sort_order`, `page`, `page_size`
- `GET /api/documents/duplicates` — groups with matching `receipt_date` + `vendor_receipt_id`
- `GET /api/documents/{id}` — full detail
- `PATCH /api/documents/{id}` — edit fields, records in `edit_history`, sets `manually_edited=true`
- `DELETE /api/documents/{id}` — soft delete
- `POST /api/documents/{id}/reprocess` — reset to `pending`, clear extracted fields
- `POST /api/documents/batch-reprocess` — reprocess by IDs or filter
- `GET /api/documents/{id}/file/{type}` — serve file (`original` or `converted`)
- `GET /api/documents/{id}/pages/{page_num}` — serve rendered page image (cached on disk)

### Export
- `POST /api/export` — accepts filter criteria + presets (`since_last_export`, `month`, `date_range`, `full_year`). Returns zip: `category/yyyy-mm-dd-vendor_receipt_id-hash.pdf` + `metadata.csv`. Updates `last_exported_date`.

### Categories
- `GET /api/categories` — list (excludes soft-deleted unless `?include_deleted=true`)
- `POST /api/categories` — create (name + description)
- `PATCH /api/categories/{id}` — rename, update description, reorder
- `DELETE /api/categories/{id}` — soft delete (rejects system categories)

### Settings
- `GET /api/settings` — all settings (API keys masked)
- `PATCH /api/settings` — update settings

### Backup
- `POST /api/backup/trigger` — trigger manual backup
- `GET /api/backup/history` — list with status/size/date
- `GET /api/backup/{id}/download` — download a backup
- `DELETE /api/backup/{id}` — delete a backup

### Queue
- `GET /api/queue/status` — depth, current document, recent completions/failures

### Stats
- `GET /api/stats/dashboard` — documents this month, expenses by category, pending review count, recent activity
- `GET /api/stats/processing-costs` — tokens and cost by model and time period

### Logs
- `GET /api/logs` — recent log entries with `limit` and `level` params

### LLM
- `POST /api/settings/test-llm` — test connectivity

---

## Frontend

React + Vite + shadcn/ui. Built to `frontend/dist/`, served by FastAPI as static files in production.

### Routes

- `/login` — login form
- `/` — dashboard: stats cards (processed this month, total expenses by category, pending review count), recent activity feed
- `/documents` — document browser: search bar, filter bar (status, category, type, date range, channel), sortable table, pagination, bulk actions (reprocess, delete), quick filter tabs (Needs Review, Failed, Possible Duplicates)
- `/documents/{id}` — document detail: left panel = page images (navigable), right panel = editable metadata form + category dropdown + status badge + confidence score + notes + edit history (collapsible)
- `/export` — filter controls + preset buttons (since last export, month, date range, year), download trigger
- `/settings` — tabbed: General | LLM | Categories | Backup | Logs

### Auth flow
Session-based with httponly cookie. Login posts to `/api/auth/login`. Frontend redirects to `/login` on 401.

---

## File Storage Layout

```
data/
├── storage/
│   ├── originals/          # Original files by hash: {sha256}.{ext}
│   ├── converted/          # Converted PDFs by hash: {sha256}.pdf
│   ├── filed/              # Human-readable names: yyyy-mm-dd-vendor_receipt_id-hash.pdf
│   └── page_cache/         # Rendered page images: {document_id}/page_{n}.png
├── receiptory.db           # SQLite database
└── logs/
    └── receiptory.log
```

Single Docker volume mount at `data/`. The `filed/` directory contains a copy of the final PDF (the converted PDF if conversion happened, otherwise the original). `originals/` and `converted/` are the canonical storage; `filed/` provides human-readable filenames for export. Page cache lazily populated on first view, invalidated on reprocess.

---

## Configuration

All settings stored in `settings` table. Precedence: environment variable (`RECEIPTORY_` prefix) > database > default.

| Key | Default | Description |
|---|---|---|
| `business_names` | `[]` | Business/personal names in multiple languages |
| `business_addresses` | `[]` | Business addresses in multiple languages |
| `business_tax_ids` | `[]` | Tax IDs for document type detection |
| `reference_currency` | `"ILS"` | For future currency conversion |
| `llm_model` | `"gemini/gemini-3-flash-preview"` | litellm model string |
| `llm_api_key` | `""` | API key for configured provider |
| `llm_sleep_interval` | `0.0` | Seconds between LLM calls |
| `confidence_threshold` | `0.7` | Below this -> `needs_review` |
| `auth_username` | `"admin"` | Web UI username |
| `auth_password_hash` | bcrypt hash of `"admin"` | Web UI password |
| `log_level` | `"INFO"` | Application log level |
| `page_render_dpi` | `200` | DPI for page image rendering |
| `backup_destination` | `""` | rclone remote path |
| `backup_schedule` | `"0 2 * * *"` | Cron expression (default 2am daily) |
| `backup_retention_daily` | `7` | Days to keep daily backups |
| `backup_retention_weekly` | `4` | Weeks to keep weekly backups |
| `backup_retention_monthly` | `3` | Months to keep monthly backups |

### Bootstrap
1. Migrations create tables and seed system categories + starter user categories.
2. If no LLM API key configured, queue processor starts but skips processing with a warning.
3. User logs in with default credentials and configures settings.

---

## Docker

Single container via Docker Compose. Includes: Python runtime, Node (build stage only), weasyprint system dependencies, rclone.

```yaml
# docker-compose.yml
services:
  receiptory:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    restart: unless-stopped
```

Multi-stage Dockerfile: Node stage builds frontend, Python stage runs the app.

---

## Development

In dev mode, Vite runs on its own port (5173) with HMR. FastAPI runs on 8080. CORS is configured in FastAPI to allow the Vite dev origin. In production, the built frontend is served by FastAPI directly so CORS is not needed.
