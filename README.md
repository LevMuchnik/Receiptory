# Receiptory

Self-hosted receipt, invoice, and document management system for self-employed professionals. Uses LLM-powered extraction to process scanned and digital documents.

## Features

- **Web upload** with drag-and-drop, SHA-256 deduplication
- **Telegram bot** — send or forward photos/documents to a bot for processing
- **Gmail ingestion** — polls a dedicated Gmail inbox for forwarded receipts and bills
- **LLM extraction** via litellm (Gemini, OpenAI, Anthropic) — OCR, field extraction, classification in one pass
- **Document browser** with full-text search (FTS5), filters, sorting, pagination
- **Document detail view** with rendered page images and editable metadata
- **Export** filtered documents as zip (category folders + CSV metadata)
- **Cloud backup** via rclone with configurable retention policy
- **Single-user auth** with session cookies

## Quick Start (Docker Compose)

```bash
cp .env.example .env
# Edit .env — at minimum set your LLM API key:
#   RECEIPTORY_LLM_API_KEY=your-key-here

docker compose up -d
```

Open http://localhost:8484. Login with `admin` / `admin`, then change the password in Settings.

## Unraid Setup

### Installation

1. Open the Unraid web UI and go to the terminal (or SSH into your server)

2. Create the app directory and clone the repo:
   ```bash
   mkdir -p /mnt/user/appdata/Receiptory
   cd /mnt/user/appdata/Receiptory
   git clone https://github.com/LevMuchnik/Receiptory.git .
   ```

3. Create and edit the environment file:
   ```bash
   cp .env.example .env
   nano .env
   ```
   At minimum, set:
   ```env
   RECEIPTORY_LLM_API_KEY=your-api-key-here
   RECEIPTORY_LLM_MODEL=gemini/gemini-3-flash-preview
   RECEIPTORY_AUTH_USERNAME=admin
   RECEIPTORY_AUTH_PASSWORD=your-password
   RECEIPTORY_PORT=8484
   ```

4. Build and start the container:
   ```bash
   cd /mnt/user/appdata/Receiptory
   docker compose up -d --build
   ```

5. Open `http://your-nas-ip:8484` in your browser

### Updating

```bash
cd /mnt/user/appdata/Receiptory
git pull
docker compose up -d --build
```

### Data Persistence

All data is stored in `/mnt/user/appdata/Receiptory/data/` which is mounted into the container. This directory survives container rebuilds and contains:

- `receiptory.db` — SQLite database
- `storage/` — uploaded and processed document files
- `logs/` — application logs
- `rclone.conf` — cloud backup credentials (auto-generated from OAuth tokens)

### Cloud Backup (Google Drive / OneDrive)

Receiptory can back up to Google Drive and/or OneDrive via OAuth. Both can be active simultaneously.

#### Google Drive Setup

1. Go to [Google Cloud Console > Credentials](https://console.cloud.google.com/apis/credentials)
2. Create a project if you don't have one (any name, e.g. "Receiptory")
3. Click **+ Create Credentials** > **OAuth client ID**
4. If prompted, configure the consent screen: choose **External**, fill in app name and email, save
5. Select **Web application** as the application type
6. Under **Authorized redirect URIs**, add:
   ```
   http://your-nas-ip:8484/api/cloud-auth/callback/gdrive
   ```
7. Click **Create** and copy the **Client ID** and **Client Secret**
8. Go to [APIs & Services > Library](https://console.cloud.google.com/apis/library/drive.googleapis.com), search for **Google Drive API**, and click **Enable**
9. In Receiptory: go to **Administration > Resilience**, paste the Client ID and Secret into the Google Drive section, then click **Connect Google Drive**

#### OneDrive Setup

1. Go to [Azure Portal > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps)
2. Click **+ New registration**
   - Name: "Receiptory" (or anything)
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
   - Redirect URI: select **Web** and enter:
     ```
     http://your-nas-ip:8484/api/cloud-auth/callback/onedrive
     ```
3. Click **Register**. Copy the **Application (client) ID**
4. Go to **Certificates & secrets** > **+ New client secret**. Copy the **Value** (not the Secret ID — the value is only shown once)
5. Go to **API permissions** > **+ Add a permission** > **Microsoft Graph** > **Delegated permissions**. Add:
   - `Files.ReadWrite.All`
   - `User.Read`
   - `offline_access`
6. In Receiptory: go to **Administration > Resilience**, paste the Client ID and Secret into the OneDrive section, then click **Connect OneDrive**

#### Backup Retention

Old backups are automatically cleaned up after each run:

| Type | When | Default Retention |
|---|---|---|
| Daily | Every day | 7 days |
| Weekly | Sundays | 4 weeks |
| Monthly | 1st of month | 3 months |
| Quarterly | Jan/Apr/Jul/Oct 1st | Never deleted |

Retention periods are configurable in **Administration > Resilience > Backup Schedule**.

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
| `RECEIPTORY_TELEGRAM_BOT_TOKEN` | (empty) | Telegram bot token from @BotFather |
| `RECEIPTORY_GMAIL_ADDRESS` | (empty) | Gmail address to poll |
| `RECEIPTORY_GMAIL_APP_PASSWORD` | (empty) | Gmail App Password (16 chars) |
| `RECEIPTORY_BACKUP_DESTINATION` | (empty) | rclone remote path (e.g., `gcs:my-bucket/receiptory`) |
| `RECEIPTORY_BACKUP_SCHEDULE` | `0 2 * * *` | Backup cron schedule |

## Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow prompts to create a bot
3. Copy the bot token into `.env` as `RECEIPTORY_TELEGRAM_BOT_TOKEN` (or set via Settings > Telegram)
4. Restart the backend
5. Optionally restrict access: add your Telegram user ID to "Authorized User IDs" in Settings > Telegram (message [@userinfobot](https://t.me/userinfobot) to find your ID). Leave empty to allow anyone.
6. Send photos or documents to your bot — they'll appear in the Documents page

## Gmail Ingestion Setup

Uses IMAP with a Gmail App Password — no Google Cloud project needed.

1. **Enable 2-Step Verification** on the Gmail account: [myaccount.google.com/security](https://myaccount.google.com/security) > 2-Step Verification > turn on
2. **Generate an App Password**: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   - Select app: "Mail", select device: "Other" (enter "Receiptory")
   - Copy the 16-character password (e.g., `abcd efgh ijkl mnop`)
3. In Receiptory Settings > Gmail (or `.env`):
   - Set the Gmail address and App Password
   - Optionally set authorized senders (e.g., `invoice@company.com; @utility.co.il`)
4. Click **Test Connection** to verify
5. The poller runs automatically (default: every 5 minutes). Use "Poll Now" for immediate check.

Unread emails with PDF/image attachments are ingested automatically. HTML-only emails (e.g., digital receipts) are saved as HTML for processing. Emails from unauthorized senders are flagged for review.

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
uv run uvicorn backend.main:create_app --factory --reload --port 8484
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run dev server with HMR (port 5173)
npm run dev
```

In dev mode, the frontend proxies API calls to `http://localhost:8484`. Both servers need to be running. Set `RECEIPTORY_DEV=1` in `.env` to prevent the backend from serving static files.

### Running Tests

```bash
uv run pytest tests/ -v
```

### Building for Production

```bash
# Build frontend
cd frontend && npm run build && cd ..

# The FastAPI app serves frontend/dist/ as static files
uv run uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8484
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
│   ├── ingestion/           # Telegram bot, Gmail poller
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
