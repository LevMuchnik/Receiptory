# Receiptory

Self-hosted receipt, invoice, and document management system for self-employed professionals. Uses LLM-powered extraction to process scanned and digital documents.

## Features

- **Web upload** with drag-and-drop, SHA-256 deduplication
- **LLM extraction** via litellm (Gemini, OpenAI, Anthropic) — OCR, field extraction, classification in one pass
- **Document browser** with full-text search (FTS5), filters, sorting, pagination
- **Document detail view** with rendered page images and editable metadata
- **Export** filtered documents as zip (category folders + CSV metadata)
- **Cloud backup** via rclone with configurable retention policy
- **Single-user auth** with session cookies

## Quick Start (Docker)

```bash
cp .env.example .env
# Edit .env — at minimum set your LLM API key:
#   RECEIPTORY_LLM_API_KEY=your-key-here

docker compose up -d
```

Open http://localhost:8080. Login with `admin` / `admin`, then change the password in Settings.

## Configuration

All settings are configurable via the admin UI (Settings page). Environment variables override DB values.

| Variable | Default | Description |
|---|---|---|
| `RECEIPTORY_LLM_API_KEY` | (empty) | API key for your LLM provider |
| `RECEIPTORY_LLM_MODEL` | `gemini/gemini-3-flash-preview` | litellm model string |
| `RECEIPTORY_AUTH_USERNAME` | `admin` | Web UI username |
| `RECEIPTORY_AUTH_PASSWORD` | `admin` | Initial password (set via UI after first login) |
| `RECEIPTORY_DATA_DIR` | `./data` | Data directory (DB, files, logs) |
| `RECEIPTORY_SECRET_KEY` | (default) | Session signing key — change in production |
| `RECEIPTORY_BACKUP_DESTINATION` | (empty) | rclone remote path (e.g., `gcs:my-bucket/receiptory`) |
| `RECEIPTORY_BACKUP_SCHEDULE` | `0 2 * * *` | Backup cron schedule |

## Development Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 20+

### Backend

```bash
# Install dependencies
uv sync --all-extras

# Run the backend (port 8080)
uv run uvicorn backend.main:create_app --factory --reload --port 8080
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run dev server with HMR (port 5173)
npm run dev
```

In dev mode, the frontend proxies API calls to `http://localhost:8080`. Both servers need to be running.

### Running Tests

```bash
uv run pytest tests/ -v
```

### Building for Production

```bash
# Build frontend
cd frontend && npm run build && cd ..

# The FastAPI app serves frontend/dist/ as static files
uv run uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8080
```

## Project Structure

```
receiptory/
├── backend/
│   ├── main.py              # FastAPI app, lifespan
│   ├── config.py            # Settings (env > db > defaults)
│   ├── auth.py              # Session auth
│   ├── database.py          # SQLite + migrations
│   ├── storage.py           # File I/O, page rendering
│   ├── models.py            # Pydantic models
│   ├── api/                 # REST endpoints
│   ├── processing/          # LLM pipeline, queue
│   └── backup/              # Scheduler, runner, rclone
├── frontend/                # React + Vite + shadcn/ui
├── migrations/              # Numbered SQL files
├── tests/                   # pytest suite
├── Dockerfile               # Multi-stage build
└── docker-compose.yml
```

## Data & Backups

All data lives in the `data/` directory (Docker volume mount):

- `data/receiptory.db` — SQLite database
- `data/storage/` — document files (originals, converted PDFs, filed copies)
- `data/logs/` — application logs

Backups include all files, the database, a JSONL metadata export (app-independent), and settings. Backups are usable without Receiptory.

## License

Private project.
