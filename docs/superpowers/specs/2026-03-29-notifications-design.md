# Receiptory Notifications — Design Spec

Configurable notification system that alerts the user via Telegram and/or Email when key events occur (document ingestion, processing, failures, backups).

---

## Architecture

New `backend/notifications/` package:

- **`notifier.py`** — central dispatcher. Exposes `notify(event_type, payload)`. Checks the preference matrix per channel, fans out to enabled channels. Fire-and-forget — errors are logged, never block the caller.
- **`telegram_notify.py`** — sends notifications via the existing Telegram bot instance (`backend/ingestion/telegram._app`). Photo message with caption for `ingested` and `processed` events (first page thumbnail). Text-only for failures and backup events.
- **`email_notify.py`** — sends HTML email via SMTP using the existing Gmail address + App Password (`smtp.gmail.com:587`). Inline thumbnail for document events. Configurable "From" display name.
- **`templates.py`** — formats notification content per event type. Generates caption text, HTML email body, subject lines, and document links using `base_url` setting.

### Integration Points

The notifier is called from:
- **`processing/pipeline.py`** — after `processed`, `failed`, or `needs_review` status is set
- **Ingestion adapters** (`ingestion/telegram.py`, `gmail.py`, `watched_folder.py`, `api/upload.py`) — after successful ingestion
- **`backup/scheduler.py`** — after backup completes or fails

All `notify()` calls are wrapped in try/except so notification failures never affect core functionality.

---

## Event Types

| Event | Key | Telegram Format | Email Subject Example |
|---|---|---|---|
| Document ingested | `ingested` | Photo + caption | "Receiptory: New document — invoice.pdf" |
| Document processed | `processed` | Photo + caption | "Receiptory: Processed — Office Depot ₪29.25" |
| Processing failed | `failed` | Text only | "Receiptory: Processing failed — file.pdf" |
| Needs review | `needs_review` | Text only | "Receiptory: Review needed — scan_001.pdf (45%)" |
| Backup completed | `backup_ok` | Text only | "Receiptory: Backup completed — 142 MB" |
| Backup failed | `backup_failed` | Text only | "Receiptory: Backup failed" |

---

## Notification Content

### Telegram — Photo messages (ingested, processed)

Photo: first page of the document rendered as PNG (reuse `storage.render_page()`).

Caption for **ingested**:
```
📥 Document Ingested
File: invoice_october.pdf
Channel: email (from noreply@company.com)
Status: Queued for processing
🔗 https://receiptory.levmuchnik.com/documents/42
```

Caption for **processed**:
```
✅ Document Processed
Vendor: Office Depot
Date: 2026-01-15 | Amount: ₪29.25
Category: office_supplies
Confidence: 95%
🔗 https://receiptory.levmuchnik.com/documents/42
```

### Telegram — Text messages (failures, backup)

```
❌ Processing Failed
File: corrupt_scan.pdf
Error: Failed to parse LLM response as JSON
Attempts: 2
🔗 https://receiptory.levmuchnik.com/documents/42
```

```
⚠️ Needs Review
File: scan_001.pdf
Vendor: Unknown | Amount: ₪0.00
Confidence: 45%
🔗 https://receiptory.levmuchnik.com/documents/42
```

```
✅ Backup Completed
Type: daily | Size: 142 MB
Destination: gcs:my-bucket/receiptory
```

```
❌ Backup Failed
Error: rclone connection refused
```

### Email

HTML email with:
- Subject: "Receiptory: [Event] — [Key Detail]"
- From: `notify_from_name <gmail_address>` (e.g., "Receiptory <levmuchnik@gmail.com>")
- Body: same content as Telegram caption, formatted in HTML
- For document events: inline thumbnail image (first page PNG) as email attachment with `Content-ID` for inline display
- Document link as clickable URL
- Sent via SMTP using existing `gmail_address` + `gmail_app_password` credentials (`smtp.gmail.com:587`, STARTTLS)
- Recipient: `notify_email_to` setting (defaults to `gmail_address` if empty)

---

## Settings

All stored in the `settings` table with `RECEIPTORY_` env var overrides.

| Setting | Default | Description |
|---|---|---|
| `base_url` | `""` | App URL for document links (e.g., `https://receiptory.levmuchnik.com`) |
| `notify_from_name` | `"Receiptory"` | Display name in email From header |
| `notify_email_to` | `""` | Email recipient (defaults to `gmail_address` if empty) |
| `notify_telegram_ingested` | `false` | |
| `notify_telegram_processed` | `false` | |
| `notify_telegram_failed` | `true` | |
| `notify_telegram_needs_review` | `true` | |
| `notify_telegram_backup_ok` | `false` | |
| `notify_telegram_backup_failed` | `true` | |
| `notify_email_ingested` | `false` | |
| `notify_email_processed` | `false` | |
| `notify_email_failed` | `true` | |
| `notify_email_needs_review` | `true` | |
| `notify_email_backup_ok` | `false` | |
| `notify_email_backup_failed` | `true` | |

Defaults: only failures and needs_review are enabled on both channels.

---

## Settings UI

New **Notifications** tab in Administration page.

Configuration fields at top:
- Base URL (for document links)
- From Name (for emails)
- Email Recipient (defaults to Gmail address)

Matrix grid:

```
Event                  Telegram    Email
─────────────────────  ─────────   ─────
Document Ingested      [ ]         [ ]
Document Processed     [ ]         [ ]
Processing Failed      [✓]         [✓]
Needs Review           [✓]         [✓]
Backup Completed       [ ]         [ ]
Backup Failed          [✓]         [✓]
```

Each cell is a checkbox. Changes save on toggle (same onBlur/save pattern as other settings).

A "Send Test Notification" button that sends a sample notification via both enabled channels.

---

## Dependencies

- **Telegram**: reuses existing bot (`python-telegram-bot`). No new dependencies.
- **Email SMTP**: uses Python's built-in `smtplib` and `email.mime`. No new dependencies.

---

## Env Vars (.env.example)

```env
# --- Notifications ---
# RECEIPTORY_BASE_URL=https://receiptory.levmuchnik.com
# RECEIPTORY_NOTIFY_FROM_NAME=Receiptory
# RECEIPTORY_NOTIFY_EMAIL_TO=                    # defaults to GMAIL_ADDRESS
# RECEIPTORY_NOTIFY_TELEGRAM_INGESTED=false
# RECEIPTORY_NOTIFY_TELEGRAM_PROCESSED=false
# RECEIPTORY_NOTIFY_TELEGRAM_FAILED=true
# RECEIPTORY_NOTIFY_TELEGRAM_NEEDS_REVIEW=true
# RECEIPTORY_NOTIFY_TELEGRAM_BACKUP_OK=false
# RECEIPTORY_NOTIFY_TELEGRAM_BACKUP_FAILED=true
# RECEIPTORY_NOTIFY_EMAIL_INGESTED=false
# RECEIPTORY_NOTIFY_EMAIL_PROCESSED=false
# RECEIPTORY_NOTIFY_EMAIL_FAILED=true
# RECEIPTORY_NOTIFY_EMAIL_NEEDS_REVIEW=true
# RECEIPTORY_NOTIFY_EMAIL_BACKUP_OK=false
# RECEIPTORY_NOTIFY_EMAIL_BACKUP_FAILED=true
```
