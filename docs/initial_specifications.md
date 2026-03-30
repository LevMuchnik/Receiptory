# Receiptory — Project Specification

A self-hosted receipt, invoice, and document management system for self-employed professionals. Receiptory ingests, processes, organizes, and exports financial and supporting documents using LLM-powered extraction.

---

## Overview

Receiptory solves a common problem for self-employed individuals: receipts, invoices, and supporting documents arrive from many sources (phone scans, email, utility providers), in varying formats and languages, and need to be organized, categorized, and exported for accounting and tax purposes.

The system runs on a home NAS (Unraid or plain Linux), is fully self-hosted, and stores all data locally with cloud backups. It uses LLMs to extract structured data from scanned and digital documents, classifies them into user-defined categories, and provides a web interface for review, search, editing, and export.

---

## Architecture

**Stack:** Python (managed with uv), FastAPI backend, React frontend, SQLite database, Docker Compose for deployment. Code lives in a GitHub repository.

**Deployment:** Self-hosted on a home NAS running Unraid or plain Linux. Optionally accessible from the web via Cloudflare Tunnel or Cloudflare Zero Trust. The application itself is network-agnostic — external access is handled entirely at the infrastructure level and does not affect application code.

**Authentication:** Single-user system. The web UI is protected by a single username/password configured in settings.

**Database:** SQLite in WAL mode for concurrent read/write. FTS5 extension for full-text search on extracted document text. Schema migrations are handled with a simple hand-rolled versioned migration system (numbered SQL files applied in order, with a `schema_version` table tracking the current version).

**LLM Layer:** Abstracted via litellm (or similar), supporting OpenAI, Gemini, and Anthropic models. The active model is configurable via the admin UI. Processing cost (tokens in/out, model used, estimated USD cost) is tracked per document. Approximate token counts are sufficient.

---

## Configuration and Secrets

All system settings — including API keys, credentials, and operational parameters — are configurable through the admin UI. Settings are stored in the SQLite database and included in backups.

For initial bootstrap or override, settings can also be provided via environment variables or a `.env` file. The precedence order is: environment variable > database setting > default value.

API keys and credentials are stored in the database in plain text. The system relies on host-level security (single-user NAS, not publicly exposed without Cloudflare) rather than application-level encryption of secrets.

---

## Ingestion

Documents enter the system through independent ingestion adapters, all feeding into a single internal processing queue (database-backed). Supported channels:

- **Web UI upload** — drag-and-drop, single or batch, from the browser.
- **Telegram bot** — send or forward photos/documents to a dedicated bot. Bot token and authorized user(s) configured in admin settings.
- **Email (Gmail)** — a dedicated Gmail address receives forwarded bills and scanned receipts. Uses polling on a configurable interval (default: every 5 minutes). Only emails from an authorized sender list are processed; unauthorized emails are logged in a separate category and visible in the UI but not processed. The authorized sender list supports domain-level rules. Gmail OAuth2 uses Google's installed-app (desktop) OAuth flow — credentials are obtained once via browser-based consent and refresh tokens are stored in the database.
- **Watched folder** — a directory on the NAS filesystem; any file dropped in is automatically picked up.

Files submitted in non-PDF formats are converted to PDF. Both the original file and the converted PDF are stored.

**Deduplication:** SHA-256 file hash is computed at ingestion. Exact duplicates are rejected and the user is notified. Near-duplicate detection (e.g., same receipt photographed twice) is not implemented — users handle this manually via the UI. Files with identical names but different content are accepted and renamed internally.

**Clarification requests (future):** When the LLM determines that a document is ambiguous or uncertain (low confidence on key fields), it can queue a clarification request. These appear in a dedicated review queue in the web UI, where the user can provide the missing information.

---

## Processing Pipeline

Documents are processed sequentially from the database-backed queue — one document at a time, no parallel processing. A configurable sleep interval (default: 0.0 seconds) is inserted between consecutive LLM calls to allow rate-limit management.

Each document in the queue is processed as follows:

1. **Format normalization** — non-PDF files are converted to PDF. HTML-only email receipts are rendered to PDF (e.g., via weasyprint). The original file is preserved alongside the converted version.

2. **LLM extraction (multi-pass)** — the document pages are rendered as images and sent to the configured LLM in multiple passes:
   - **Pass 1 — OCR and data extraction:** Extract all structured fields (dates, amounts, vendor info, line items) plus a full-text OCR dump.
   - **Pass 2 — Classification and confidence:** Determine document type, assign category, and provide confidence scores.

   This separation allows each pass to have a focused prompt and makes it easier to iterate on prompt quality independently.

3. **Document type detection** — the system determines the document type by matching the vendor's tax ID against the user's own business identifications (configured in system settings):
   - **Expense receipt** — vendor tax ID does not match the user's business IDs.
   - **Issued invoice** — vendor tax ID matches the user's business IDs.
   - **Other document** — non-financial documents (flight tickets, contracts, certificates, etc.) that don't fit the receipt/invoice pattern. The LLM extracts dates and key-value pairs as available.

4. **Category classification** — the LLM assigns a category from the user-configurable category list. Some system categories are fixed: `pending`, `not_a_receipt`, `unauthorized_sender`, `failed`.

5. **Confidence scoring** — the LLM provides an overall extraction confidence score (0–1). Documents below a configurable threshold are flagged for review.

6. **Filing** — the document is stored with a system-generated filename: `yyyy-mm-dd-vendor_receipt_id-hash.pdf` (with `0000-00-00` if date is unknown, `000000` if vendor receipt ID is not extracted).

7. **Failure handling** — if processing fails, the document entry is preserved with status `failed`, the error message is recorded, and the processing attempt count is incremented.

**Reprocessing:** Any document (or batch of documents) can be reprocessed from the web UI. This is useful after prompt improvements, LLM model changes, or category updates.

---

## Document Schema

### Identity and Filing

| Field | Description |
|---|---|
| `document_id` | Unique auto-generated primary key |
| `document_type` | `expense_receipt`, `issued_invoice`, or `other_document` (detected via vendor tax ID match and LLM classification) |
| `original_filename` | Filename as submitted |
| `stored_filename` | System filename: `yyyy-mm-dd-vendor_receipt_id-hash.pdf` |
| `file_hash` | SHA-256 of the original file (deduplication key) |
| `file_size_bytes` | Size of the stored file |
| `page_count` | Number of pages in the document |

### Ingestion Metadata

| Field | Description |
|---|---|
| `submission_date` | When the system received the document |
| `submission_channel` | Enum: `telegram`, `email`, `web_upload`, `watched_folder` |
| `sender_identifier` | Telegram user, email address, or null for local channels |

### Extracted Fields (LLM Output)

| Field | Description |
|---|---|
| `receipt_date` | Date printed on the document |
| `document_title` | Title as it appears on the document (e.g., "חשבונית מס", "קבלה", "invoice", "boarding pass") |
| `vendor_name` | Vendor/issuer name as extracted |
| `vendor_tax_id` | Business number / ע.מ. / ח.פ. (the user's own business for issued invoices) |
| `vendor_receipt_id` | Receipt/invoice number printed on the document |
| `client_name` | Client name (for issued invoices); buyer name on receipts if present |
| `client_tax_id` | Client tax ID (for issued invoices); buyer tax ID on receipts if present |
| `description` | Brief summary of the purchase, service, or document contents |
| `line_items` | JSON array: `[{"description": "...", "quantity": N, "unit_price": N}, ...]` |
| `subtotal` | Pre-tax amount (null for non-financial documents) |
| `tax_amount` | Tax amount (null for non-financial documents) |
| `total_amount` | Total amount (null for non-financial documents) |
| `currency` | ISO 4217 code (ILS, USD, EUR, etc.) |
| `converted_amount` | Amount in the reference currency (optional) |
| `conversion_rate` | Exchange rate used for conversion (optional) |
| `payment_method` | Cash, credit card, bank transfer, etc. (if detectable) |
| `payment_identifier` | Credit card last digits, bank account number, etc. |
| `language` | Detected document language (he, en, ru, etc.) |
| `additional_fields` | JSON array: `[{"key": "...", "value": "..."}, ...]` for any other extracted data (addresses, phone numbers, flight numbers, seat assignments, etc.) |
| `raw_extracted_text` | Full OCR text, indexed by FTS5 for search |

### Classification

| Field | Description |
|---|---|
| `category` | FK to user-configurable categories table |
| `status` | Enum: `pending`, `processed`, `failed`, `needs_review`, `unauthorized_sender`, `not_a_receipt` |

### Confidence and Processing

| Field | Description |
|---|---|
| `extraction_confidence` | Overall confidence score (0–1) |
| `field_confidences` | JSON object with per-field scores (deferred — not implemented in v1) |
| `processing_model` | LLM model used (e.g., `gpt-4o`, `gemini-1.5-pro`) |
| `processing_tokens_in` | Input tokens consumed (approximate) |
| `processing_tokens_out` | Output tokens consumed (approximate) |
| `processing_cost_usd` | Estimated cost for this extraction (approximate) |
| `processing_date` | When extraction was performed |
| `processing_attempts` | Number of processing attempts |
| `processing_error` | Error message if failed, null otherwise |

### Manual Overrides and Audit

| Field | Description |
|---|---|
| `manually_edited` | Boolean flag |
| `is_deleted` | Soft delete flag |
| `edit_history` | JSON array: `[{"field": "...", "old_value": "...", "new_value": "...", "timestamp": "..."}, ...]` |
| `user_notes` | Free-text user notes |

### Export Tracking

| Field | Description |
|---|---|
| `last_exported_date` | When this document was last included in an export |

### Housekeeping

| Field | Description |
|---|---|
| `created_at` | Record creation timestamp |
| `updated_at` | Last modification timestamp |

---

## Currency Conversion

Exchange rates are fetched from a free API (e.g., frankfurter.app or exchangerate.host) on demand and cached locally in the database. When a document's currency differs from the user's configured reference currency (default: ILS), the system fetches the rate for the document's date (or nearest available date), converts the amount, and stores both `converted_amount` and `conversion_rate`.

Cached rates are reused for subsequent documents with the same currency and date. Rate lookups that fail (API down, date unavailable) are logged but do not block document processing — the conversion fields are left null and can be filled later via reprocessing.

---

## Storage and Backup

**Local storage:** Document files are stored on the NAS filesystem with content-hashed filenames. Both the original submitted file and the converted PDF (if applicable) are kept. Metadata is stored in the SQLite database.

**Backups** are performed to cloud storage (GCP, Azure, or any rclone-compatible target) on a configurable schedule. Each backup contains:

- All document files (originals and converted PDFs)
- The SQLite database file
- A JSONL export of all metadata (human-readable, app-independent, with a documented schema)
- System configuration (all settings from the database)
- Log files

**Backup retention policy (default):**

- **Daily backups:** retained for 7 days
- **Weekly backups** (Sunday): retained for 4 weeks
- **Monthly backups** (1st of month): retained for 3 months
- **Quarterly backups** (Jan 1, Apr 1, Jul 1, Oct 1): retained permanently

The retention policy is configurable via admin settings.

**Backup principle:** Backups are usable without the application. The JSONL export uses a documented, stable schema so data can be recovered even without running Receiptory. The full system state can be restored from a backup. The admin UI shows backup history, success/failure status, and allows triggering, downloading, or deleting individual backups.

---

## Web UI

**Tech:** FastAPI backend serving a React frontend. Single-user password protection. Mobile-friendly (responsive design).

### Dashboard (Landing Page)

At-a-glance overview: documents processed this month, total expenses by category, items pending review, recent ingestion activity.

### Document Browser

Browse, search (full-text via FTS5), filter and sort documents. Filters include: status, category, document type (expense receipt, issued invoice, other document), date range, submission channel. Each document shows its scanned image alongside extracted metadata. Users can edit any metadata field, add notes, change categories, and mark documents as deleted.

Special views: failed/unprocessed documents, possible duplicates (by receipt date + vendor receipt ID), documents missing date or ID.

### Export

Export any filtered subset as a zip file containing:

- Directory structure: `category/yyyy-mm-dd-vendor_receipt_id-hash.pdf`
- A CSV file with all metadata for the exported documents

Quick export presets:

- All since last export
- Specific month (by receipt date)
- Date range
- Full year

Exporting updates the `last_exported_date` field on each included document.

### Administration

- **Connections:** Test Gmail connection, Telegram bot status.
- **LLM:** Test LLM connectivity, view processing cost summary, configure inter-call sleep interval.
- **Backups:** Trigger backup, view backup log (success/failure), list/download/delete backups, configure retention policy.
- **Logging:** View log file, configure log detail level (app and Docker).
- **Categories:** Manage the user-configurable category list.
- **Settings:** Authorized email senders, business tax IDs, reference currency, LLM model selection and API keys, backup schedule and destination, Gmail OAuth credentials, Telegram bot token and authorized users, confidence threshold for review flagging.

### Audit Trail

All manual edits and reprocessing events are recorded in each document's `edit_history` field. This trail is hidden by default in the UI but accessible when needed.

---

## System Configuration

The following settings are user-configurable (via admin UI, with environment variable / `.env` override):

- **Business identifiers** — the user's own tax IDs, used to detect issued invoices vs. expense receipts
- **Authorized email senders** — allowlist with support for domain-level rules
- **Expense categories** — user-defined list (plus fixed system categories)
- **Reference currency** — for currency conversion (default: ILS)
- **LLM settings** — model selection, API keys, inter-call sleep interval (default: 0.0s), confidence threshold for review flagging
- **Telegram bot** — bot token and authorized user(s)
- **Gmail** — OAuth2 credentials (installed-app flow), polling interval (default: 5 minutes)
- **Backup** — destination (rclone target), schedule, retention policy
- **Logging** — detail level for application and Docker logs
- **Web UI** — username and password

---

## Testing

The project includes:

- **Unit tests** — for the extraction/processing pipeline (given a sample document, verify structured output matches expected fields)
- **Integration tests** — for each ingestion adapter
- **End-to-end tests** — ingest a file through the queue, verify it is processed and stored correctly in the database

A `test_documents/` folder in the repository contains sample receipts, invoices, and other documents used as test fixtures for validating LLM extraction and the full processing pipeline.

---

## Future Considerations

- **Gmail push notifications** — upgrade from polling to Google Cloud Pub/Sub push for near-instant email pickup (requires publicly reachable endpoint via Cloudflare)
- **Clarification workflow** — LLM-driven requests for user input on ambiguous documents, queued in the web UI
- **Per-field confidence scores** — schema field `field_confidences` is reserved but not implemented in v1
- **Vendor normalization** — canonical vendor names for consistent grouping
- **Recurring expense detection** — flag when expected periodic bills are missing
- **Near-duplicate detection** — flag visually similar documents (e.g., same receipt photographed twice)
  