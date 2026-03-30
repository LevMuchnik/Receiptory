# CLAUDE.md

## Project Overview

Receiptory is a self-hosted receipt, invoice, and document management system for self-employed professionals. It uses LLM-powered extraction (via litellm) to process scanned and digital documents. Single-user system designed to run on a home NAS.

## Tech Stack

- **Backend:** Python 3.12+, FastAPI, SQLite (WAL + FTS5), litellm, PyMuPDF, Pillow, weasyprint
- **Frontend:** React 18, TypeScript, Vite, shadcn/ui (base-ui variant), Tailwind CSS v4
- **Package management:** uv (Python), npm (frontend)
- **Deployment:** Docker Compose, single container

## Project Structure

```
backend/                 # FastAPI application
  main.py               # App factory, lifespan, route registration
  config.py             # Settings with env > db > default precedence
  auth.py               # Session-based auth (bcrypt + itsdangerous)
  database.py           # SQLite connection, WAL, migration runner
  storage.py            # File I/O, page rendering (PyMuPDF)
  models.py             # Pydantic request/response models
  api/                  # REST endpoint routers
  processing/           # LLM pipeline, queue, normalize, extract, filing
  backup/               # Scheduler, runner, rclone wrapper
frontend/src/           # React SPA
  lib/api.ts            # Fetch wrapper with auth handling
  contexts/             # AuthContext
  pages/                # Page components
  components/           # Reusable UI components
migrations/             # Numbered SQL files (001_initial_schema.sql, ...)
tests/                  # pytest test suite
```

## Development Commands

```bash
# Backend
uv sync --all-extras                    # Install dependencies
uv run uvicorn backend.main:create_app --factory --reload --port 8484

# Frontend (separate terminal)
cd frontend && npm install && npm run dev

# Tests
uv run pytest tests/ -v
uv run pytest tests/test_e2e.py -v      # E2E only

# Build frontend for production
cd frontend && npm run build
```

## Environment

- `.env` file is loaded automatically by litellm on import. All settings use `RECEIPTORY_` prefix.
- `RECEIPTORY_DEV=1` must be set during development to disable static file serving (otherwise the `frontend/dist` mount intercepts API routes).
- `RECEIPTORY_LLM_API_KEY` is required for document processing.

## Architecture Notes

- **Single process:** FastAPI app with background asyncio tasks for the processing queue and backup scheduler. No separate worker or message broker.
- **Database:** SQLite in WAL mode. Hand-rolled migrations (numbered SQL files in `migrations/`). `schema_version` table tracks applied versions. Global `_db_path` is set by `init_db()` and protected by a threading lock.
- **Processing pipeline:** Documents are processed sequentially. Queue polls for `status='pending'`, sets to `processing`, runs normalize → LLM extract → file → update DB. Failures set `status='failed'` with error message.
- **LLM extraction:** Single-pass via litellm. Prompt includes user's business info (names, addresses, tax IDs in multiple languages) and category list with descriptions. Response is JSON parsed into `ExtractionResult` dataclass.
- **Static files in production:** `app.mount("/", StaticFiles(...))` serves `frontend/dist/`. This catch-all mount MUST be registered last (after all API routes) and is disabled when `RECEIPTORY_DEV=1`.

## Testing

- Tests use pytest with `tmp_path` fixtures for isolated SQLite databases.
- An `autouse` fixture in `conftest.py` resets `_db_path` to None and clears all `RECEIPTORY_*` env vars before each test (litellm auto-loads `.env` on import, which pollutes the test environment).
- `test_normalize.py::test_html_to_pdf` is skipped on Windows (weasyprint requires GTK/Pango native libs). Passes in Docker/Linux.
- Backend API tests use `create_app(data_dir, run_background=False)` with `TestClient`.

## Key Design Decisions

- **Config precedence:** environment variable > database setting > default value
- **Categories:** soft-delete (`is_deleted` flag). System categories (`pending`, `not_a_receipt`, `failed`) cannot be deleted. Category `description` field is fed into the LLM prompt to guide classification.
- **Document type detection:** LLM classifies, but code overrides to `issued_invoice` if `vendor_tax_id` matches any of the user's `business_tax_ids`.
- **Deduplication:** SHA-256 file hash. Exact duplicates rejected at upload.
- **Filing:** Stored as `yyyy-mm-dd-vendor_receipt_id-hash.pdf`. Three copies: `originals/` (by hash), `converted/` (if format conversion), `filed/` (human-readable name).
- **FTS5:** Virtual table indexes `raw_extracted_text`, `vendor_name`, `description`, `document_title`. Sync triggers on insert/update/delete.

## Specs and Plans

- Design spec: `docs/superpowers/specs/2026-03-28-receiptory-v1-design.md`
- Implementation plan: `docs/superpowers/plans/2026-03-28-receiptory-v1.md`
- Original requirements: `docs/initial_specifications.md`

## Gotchas

- litellm loads `.env` on import, setting `RECEIPTORY_*` env vars globally. This affects config precedence — env vars override DB values. In tests, the autouse fixture clears these.
- The `frontend/dist/` directory persists after builds. If present, the static files mount activates. Always set `RECEIPTORY_DEV=1` when running backend + Vite dev server together.
- SQLite `executescript()` auto-commits and can interfere with transaction isolation. The migration runner uses a dedicated connection that's closed after migrations.
