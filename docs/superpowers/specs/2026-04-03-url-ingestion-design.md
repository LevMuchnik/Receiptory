# URL-Based Document Ingestion

**Date:** 2026-04-03
**Status:** Draft

## Problem

Some businesses send receipts and invoices as URLs embedded in emails or Telegram messages rather than as file attachments. Current ingestion pipelines only handle direct file attachments (photos, documents, PDFs). Messages containing only URLs to receipts are silently ignored.

Additionally, emails may contain a mix of attachments and URLs where the actual receipt is behind a link while the attachment is unrelated (e.g., a licensing PDF, a promotional image). The system needs to intelligently determine what to ingest.

## Goals

- Accept text-only Telegram messages containing URLs to receipts/invoices
- Accept emails where the receipt is a linked document rather than an attachment
- Use LLM triage to determine which content (attachments vs URLs) to ingest when ambiguous
- Handle URLs that return PDFs, images, HTML pages, or JavaScript-rendered receipt portals
- Gracefully handle auth-gated URLs by saving a best-effort PDF and flagging for review

## Non-Goals

- Authenticating to third-party services to retrieve gated receipts
- Handling CAPTCHAs or multi-step download flows
- Parsing receipt data from the fetched page (that's the existing extraction pipeline's job)

---

## Design

### 1. URL Fetcher (`backend/ingestion/url_fetcher.py`)

Shared module for resolving a URL into a downloadable document. Used by both Telegram and email pipelines.

**Fetch pipeline (executed in order, stopping at first success):**

1. **HTTP fetch** — `httpx.AsyncClient.get(url)` with configurable timeout (default 5s, setting: `url_fetch_timeout`).
2. **Content-type check:**
   - `application/pdf` or `image/*` → save response body directly. Done.
   - `text/html` → continue to step 3.
   - Other → save raw response, let normalize step handle or reject it.
3. **HTML link scan** — parse the HTML with BeautifulSoup. Look for `<a>` tags whose `href` ends in `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, or contains common download indicators (e.g., `download`, `invoice`, `receipt` in the URL or link text). If a candidate link is found, fetch it and check content-type. If it yields a document, done.
4. **Playwright render** — launch headless Chromium, navigate to the URL with the same timeout. After page load:
   - **Auth wall detection:** check for password input fields (`input[type="password"]`). If detected → capture page as PDF, return with `auth_wall=True` flag.
   - **Download link scan:** scan rendered DOM for download links (same heuristics as step 3). If found, download via Playwright's request context.
   - **Page capture fallback:** if no download link found, capture the rendered page as PDF via `page.pdf()`.
5. **Result:** returns a `FetchResult` dataclass:
   ```python
   @dataclass
   class FetchResult:
       file_path: Path          # Temp file with fetched content
       content_type: str        # MIME type
       original_url: str        # Source URL
       auth_wall: bool = False  # True if login page detected
       method: str = ""         # "direct", "link_follow", "playwright_download", "playwright_capture"
   ```

**Error handling:**
- Timeout → skip URL, log warning
- HTTP 4xx/5xx → skip URL, log warning
- Playwright crash → skip URL, log error
- All errors are non-fatal; the pipeline continues with remaining URLs

### 2. URL Triage (`backend/ingestion/url_triage.py`)

Lightweight LLM calls (text-only, no vision) to determine what to ingest.

**`triage_telegram_urls(message_text: str, urls: list[str]) -> list[str]`**

Prompt: given a Telegram message and its URLs, return which URLs are likely links to receipt/invoice/financial documents. Returns filtered URL list.

**`triage_email(body_text: str, attachments: list[dict], urls: list[str]) -> TriageResult`**

Prompt: given an email body, attachment descriptions (filename, MIME type, size), and extracted URLs, decide what to ingest. Returns:

```python
@dataclass
class TriageResult:
    ingest_attachments: list[str]  # Filenames to ingest
    ingest_urls: list[str]         # URLs to fetch and ingest
```

The LLM can return any combination — some attachments, some URLs, all, or none.

**LLM configuration:** uses the same `litellm` integration and model settings as the extraction pipeline. These are short text-only prompts, so token cost is negligible.

### 3. Telegram Pipeline Changes (`backend/ingestion/telegram.py`)

**New handler: `handle_text`**

Registered with `filters.TEXT & ~filters.COMMAND` (text messages that aren't bot commands).

Flow:
1. Extract URLs from message text (regex for `https?://...`)
2. If no URLs found → ignore message (reply with "No document or URL found")
3. Call `triage_telegram_urls(message_text, urls)` → get list of receipt URLs
4. If triage returns empty → reply "No receipt/invoice URLs identified"
5. For each identified URL:
   - Call `url_fetcher.fetch_url(url)`
   - On success: compute hash, check duplicates, save original, create document record with `status='pending'`
   - If `auth_wall=True`: set `status='needs_review'`, add note about auth-gated URL
   - Reply with document ID or error
6. `submission_channel = 'telegram'`, `source_url` stored on document

**Existing handlers unchanged:**
- `handle_document` and `handle_photo` continue to work as today
- If a message has an attachment, Telegram dispatches to the attachment handler, not `handle_text`
- URL triage only runs for text-only messages

### 4. Email Pipeline Changes (`backend/ingestion/gmail.py`)

Replace the current `_process_message()` attachment/body logic with:

1. **Collect attachments** — walk MIME parts, gather all attachments with filename, MIME type, and size
2. **Extract URLs from body** — parse both `text/plain` and `text/html` parts for URLs
3. **Decision tree:**

   | Attachments | URLs | Action |
   |------------|------|--------|
   | Yes | Yes | LLM triage → ingest what it recommends (attachments, URLs, or both) |
   | Yes | No | Ingest all attachments as today |
   | No | Yes | LLM triage on URLs (same as Telegram `triage_telegram_urls`) |
   | No | No | Create document record with `status='needs_review'`, store email subject/body as context |

4. **URL fetching** — for URLs selected by triage, run through `url_fetcher.fetch_url()`. Same hash/dedup/save flow as Telegram.
5. **Auth wall handling** — same as Telegram: `needs_review` + note.
6. `submission_channel = 'email'`, `source_url` stored on document.

### 5. Database Changes

**New column on `documents` table:**

```sql
ALTER TABLE documents ADD COLUMN source_url TEXT DEFAULT NULL;
```

Stores the URL a document was fetched from. `NULL` for directly uploaded/attached files.

**Migration:** `migrations/004_add_source_url.sql`

### 6. Configuration Changes (`backend/config.py`)

New setting in `DEFAULTS`:

```python
"url_fetch_timeout": "5"  # seconds, used by url_fetcher
```

### 7. Docker Changes (`Dockerfile`)

Add Playwright and Chromium:

```dockerfile
# In Python dependencies section
RUN pip install playwright && playwright install chromium --with-deps
```

This adds ~200-300MB to the image. Chromium is installed once at build time.

### 8. Normalization

No changes to `processing/normalize.py`. Fetched files are saved as their native format (PDF, image, HTML) and the existing normalization step converts them to PDF as needed.

### 9. Dependencies

**New Python packages:**
- `playwright` — headless browser for JS-rendered pages
- `beautifulsoup4` — HTML parsing for download link detection
- `httpx` — async HTTP client (may already be a transitive dependency via FastAPI)

---

## Data Flow

### Telegram Text Message

```
Text message with URLs
        |
  Extract URLs from text
        |
  triage_telegram_urls(text, urls)  [LLM]
        |
  For each receipt URL:
        |
  url_fetcher.fetch_url(url)
    |-> Direct PDF/image download
    |-> HTML link scan -> follow link
    |-> Playwright render -> download or capture
        |
  Hash / dedup / save original
        |
  documents table (status='pending' or 'needs_review' if auth wall)
        |
  Existing processing pipeline
```

### Email with Attachments + URLs

```
Email received via IMAP
        |
  Collect attachments + extract URLs from body
        |
  [Has attachments AND URLs?]
    |-> Yes: triage_email(body, attachments, urls)  [LLM]
    |        -> Returns which attachments + which URLs to ingest
    |-> [Has attachments only?]
    |    -> Ingest attachments as today
    |-> [Has URLs only?]
    |    -> triage_telegram_urls(body, urls)  [LLM]
    |-> [Neither?]
    |    -> Create record with status='needs_review'
        |
  For selected attachments: existing attachment ingestion
  For selected URLs: url_fetcher.fetch_url() -> hash/dedup/save
        |
  documents table (status='pending' or 'needs_review')
        |
  Existing processing pipeline
```

---

## Error Handling

- **URL fetch timeout/failure:** logged, URL skipped, other URLs still processed. If all URLs fail for a message, user is notified (Telegram reply / document marked failed).
- **LLM triage failure:** fall back to ingesting all attachments (email) or all URLs (Telegram). Log the triage error.
- **Playwright failure:** falls through to HTML-to-PDF of the raw HTTP response. If HTTP also failed, URL is skipped.
- **Auth-gated pages:** saved as best-effort PDF, document created with `status='needs_review'` and a note explaining the URL appeared to require authentication.
- **Duplicate detection:** same SHA-256 hash check as existing pipeline. If a URL-fetched file matches an existing document, it's rejected as duplicate.

## Testing

- **Unit tests for `url_fetcher`:** mock HTTP responses (PDF, HTML with download links, HTML without links) and verify correct fetch method selection. Mock Playwright for JS-rendered page tests.
- **Unit tests for `url_triage`:** mock LLM responses and verify correct triage decisions for various email/Telegram scenarios.
- **Integration tests:** test the full flow from Telegram text message → URL fetch → document creation. Test email with mixed attachments + URLs → triage → selective ingestion.
- **Edge cases:** URLs that redirect, URLs that return non-document content, empty responses, very slow URLs (timeout), auth walls.
