# Smart Email Ingestion Pipeline

## Problem

When an email contains both a PDF attachment (the actual invoice) and URLs, the current triage ingests everything — the attachment plus multiple documents fetched from URLs (duplicate invoice, app UI pages, etc.). Users must manually delete the junk. The triage also has no way to determine if what it fetched is actually a receipt/invoice.

## Solution

Replace the single-pass triage with a two-phase **attachments-first** pipeline:

1. Classify attachments using LLM with full email context
2. Only fall back to URL processing if no attachment qualifies

Add a "nothing found" notification so users know when an email produced no ingested documents.

## Phase 1: Attachment Classification

When an email has attachments (PDFs, images):

1. Render the first page of each attachment to a PNG image (using existing `render_all_pages_to_memory` with page limit of 1)
2. Make one LLM call with:
   - Sender email address
   - Email subject
   - Email body (truncated to 3000 chars)
   - For each attachment: filename, content type, file size, and first-page image (base64 PNG)
3. LLM responds with which attachments (by filename) are financial documents — receipts, invoices, flight tickets, purchase confirmations — or an empty list meaning "none qualify"
4. If any attachment qualifies → ingest those attachments, **skip URLs entirely**, done
5. If none qualify → proceed to Phase 2

### Classification Prompt

The LLM receives full email context to make informed decisions. For example:
- Email from "billing@saas.com" with subject "Your monthly invoice" and a PDF named "invoice_march.pdf" → clearly a real invoice
- Same email also has "logo.png" (5KB) → not a document
- A forwarded newsletter with a random PDF attachment about product features → not a financial document

The prompt asks the LLM to identify documents that are: receipts, invoices (incoming or issued), flight/travel tickets, purchase confirmations, financial statements, tax documents, or similar financial/transactional documents.

## Phase 2: URL Fallback

When no attachment qualified (or email has no attachments):

### Step 2a: URL Pre-filtering (LLM)

Send one LLM call with:
- Sender email address
- Email subject  
- Email body (truncated to 3000 chars)
- List of extracted URLs

The LLM decides which URLs are likely to point to viewable/downloadable financial documents. Filters out: unsubscribe links, account management, marketing, social media, app store links, tracking/shipping links, general web pages.

This replaces the existing `triage_email()` function with a more focused prompt that receives sender/subject context.

### Step 2b: Fetch Candidate URLs

Fetch each candidate URL using the existing `fetch_url()` cascade (direct HTTP → link scanning → Playwright). 

**Change:** Use desktop User-Agent for email-sourced URLs (receipt portals are typically desktop-oriented). Telegram-sourced URLs keep mobile User-Agent (shared from phones).

### Step 2c: Classify Fetched Documents

Run the same classifier from Phase 1 on the fetched documents:
- Same email context (sender, subject, body)
- For each fetched document: source URL, first-page image
- LLM returns which fetched documents are actual financial documents
- Ingest only those that qualify

## Phase 3: Notification

### Existing (unchanged)
- Per-document "ingested" notification when a document is saved to DB
- Per-document "processed"/"needs_review"/"failed" notifications after extraction pipeline

### New: "nothing_found" notification
- Triggered when an email completes both phases and nothing was ingested
- Contains: sender email, subject, number of attachments checked, number of URLs checked
- Configurable via `notify_telegram_nothing_found` and `notify_email_nothing_found` settings (default: enabled for Telegram, disabled for email)
- Lets users know to manually check the email if needed

## New Function: `classify_email_documents()`

Location: `backend/ingestion/url_triage.py`

```python
async def classify_email_documents(
    sender_email: str,
    subject: str,
    body_text: str,
    documents: list[ClassificationDocument],
) -> list[str]:
    """Classify which documents are real financial documents.
    
    Each document has: identifier (filename or URL), source ("attachment" or "url"),
    and first_page_image (base64 PNG bytes).
    
    Returns list of identifiers that are actual financial documents.
    """
```

Called twice with different inputs:
- Phase 1: documents from email attachments (identifier = filename)
- Phase 2c: documents fetched from URLs (identifier = source URL)

Fallback on LLM failure: return all documents (existing behavior — ingest everything rather than silently drop).

## Changes Summary

| File | Change |
|------|--------|
| `backend/ingestion/url_triage.py` | Add `classify_email_documents()`. Update URL triage to accept sender/subject. Old `triage_email()` removed. |
| `backend/ingestion/gmail.py` | Rewrite `_process_message()`: Phase 1 (classify attachments) → Phase 2 (triage URLs, fetch, classify) → Phase 3 (notify if nothing). |
| `backend/ingestion/url_fetcher.py` | Add `user_agent` parameter to `fetch_url()` to support desktop vs mobile mode. |
| `backend/notifications/templates.py` | Add `format_nothing_found()` template. |
| `backend/notifications/notifier.py` | Register `nothing_found` event in FORMATTERS. |
| `backend/config.py` | Add `notify_telegram_nothing_found` (default True) and `notify_email_nothing_found` (default False) settings. |
| `tests/test_email_classification.py` | Tests for two-phase flow, classification function, nothing_found notification. |

## Unchanged

- `fetch_url()` internal cascade logic (direct → link scan → Playwright)
- `triage_telegram_urls()` — Telegram has its own flow, unchanged
- Processing pipeline (`pipeline.py`, `extract.py`) — unchanged
- Existing per-document notifications — unchanged
- URL SSRF protection — unchanged
