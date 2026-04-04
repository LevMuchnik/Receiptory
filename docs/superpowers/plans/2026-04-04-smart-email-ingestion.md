# Smart Email Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-pass email triage with a two-phase attachments-first pipeline that classifies documents via LLM with full email context, falling back to URL processing only when no attachment qualifies.

**Architecture:** New `classify_email_documents()` function sends email metadata + first-page images to LLM to identify financial documents. `_process_message()` in gmail.py is rewritten to: (1) classify attachments, (2) if none qualify, triage URLs via LLM with email context then fetch and classify results. A "nothing_found" notification fires when no document is ingested.

**Tech Stack:** Python, FastAPI, litellm, PyMuPDF (fitz), Pillow, pytest

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/ingestion/url_triage.py` | New `classify_email_documents()`, updated `triage_email_urls()` (replaces old `triage_email()`), keep `triage_telegram_urls()` unchanged |
| `backend/ingestion/gmail.py` | Rewritten `_process_message()` with two-phase flow, new `_render_first_page()` helper |
| `backend/ingestion/url_fetcher.py` | Add `user_agent` parameter to `fetch_url()` and `_playwright_fetch()` for desktop vs mobile mode |
| `backend/notifications/templates.py` | New `format_nothing_found()` template |
| `backend/notifications/notifier.py` | Register `nothing_found` event |
| `backend/config.py` | Two new notification settings |
| `tests/test_email_classification.py` | Tests for classify and triage functions |
| `tests/test_gmail_smart_ingestion.py` | Tests for the two-phase `_process_message()` flow |

---

### Task 1: Add `classify_email_documents()` to url_triage.py

**Files:**
- Modify: `backend/ingestion/url_triage.py`
- Test: `tests/test_email_classification.py`

- [ ] **Step 1: Write the test file with tests for `classify_email_documents()`**

Create `tests/test_email_classification.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from backend.ingestion.url_triage import classify_email_documents, ClassificationDocument


@pytest.fixture
def mock_llm_settings(monkeypatch):
    """Provide LLM settings for triage tests."""
    monkeypatch.setenv("RECEIPTORY_LLM_MODEL", "test-model")
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "test-key")


def _make_doc(identifier: str, source: str = "attachment", image: bytes = b"fake-png") -> ClassificationDocument:
    return ClassificationDocument(identifier=identifier, source=source, first_page_image=image)


@pytest.mark.asyncio
async def test_classify_returns_matching_identifiers(mock_llm_settings):
    """LLM picks invoice.pdf from a list of attachments."""
    docs = [_make_doc("invoice.pdf"), _make_doc("logo.png")]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["invoice.pdf"]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await classify_email_documents(
            sender_email="billing@vendor.com",
            subject="Your Invoice #123",
            body_text="Please find your invoice attached.",
            documents=docs,
        )
    assert result == ["invoice.pdf"]


@pytest.mark.asyncio
async def test_classify_returns_empty_when_none_qualify(mock_llm_settings):
    """LLM returns empty list when no document is financial."""
    docs = [_make_doc("newsletter.pdf")]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '[]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await classify_email_documents(
            sender_email="news@company.com",
            subject="Weekly Newsletter",
            body_text="Here is our newsletter.",
            documents=docs,
        )
    assert result == []


@pytest.mark.asyncio
async def test_classify_filters_invalid_identifiers(mock_llm_settings):
    """LLM returns an identifier not in the input — it gets filtered out."""
    docs = [_make_doc("real.pdf")]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["real.pdf", "hallucinated.pdf"]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await classify_email_documents(
            sender_email="a@b.com", subject="test", body_text="", documents=docs,
        )
    assert result == ["real.pdf"]


@pytest.mark.asyncio
async def test_classify_fallback_on_llm_failure(mock_llm_settings):
    """On LLM error, fallback returns all identifiers."""
    docs = [_make_doc("a.pdf"), _make_doc("b.png")]
    with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("API down")):
        result = await classify_email_documents(
            sender_email="a@b.com", subject="test", body_text="", documents=docs,
        )
    assert set(result) == {"a.pdf", "b.png"}


@pytest.mark.asyncio
async def test_classify_fallback_when_llm_not_configured():
    """No LLM configured — fallback returns all identifiers."""
    docs = [_make_doc("a.pdf")]
    result = await classify_email_documents(
        sender_email="a@b.com", subject="test", body_text="", documents=docs,
    )
    assert result == ["a.pdf"]


@pytest.mark.asyncio
async def test_classify_empty_documents():
    """No documents provided — returns empty list."""
    result = await classify_email_documents(
        sender_email="a@b.com", subject="test", body_text="", documents=[],
    )
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_email_classification.py -v`
Expected: ImportError — `classify_email_documents` and `ClassificationDocument` don't exist yet.

- [ ] **Step 3: Implement `ClassificationDocument` dataclass and `classify_email_documents()`**

In `backend/ingestion/url_triage.py`, add after the existing `TriageResult` dataclass (around line 18):

```python
@dataclass
class ClassificationDocument:
    identifier: str  # filename (for attachments) or URL (for fetched docs)
    source: str  # "attachment" or "url"
    first_page_image: bytes  # PNG bytes of first page
```

Add the new function after `triage_telegram_urls()` (after line 76):

```python
async def classify_email_documents(
    sender_email: str,
    subject: str,
    body_text: str,
    documents: list[ClassificationDocument],
) -> list[str]:
    """Classify which documents are real financial documents using LLM with email context.

    Sends email metadata + first-page images to LLM. Returns list of identifiers
    (filenames or URLs) that are actual financial documents.

    Fallback on LLM failure or missing config: returns ALL identifiers.
    """
    if not documents:
        return []

    all_identifiers = [d.identifier for d in documents]
    fallback = list(all_identifiers)

    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")

    if not model or not api_key:
        logger.warning("LLM not configured for document classification, returning all")
        return fallback

    truncated_body = body_text[:3000] if body_text else ""

    doc_descriptions = "\n".join(
        f"- {d.identifier} (source: {d.source})"
        for d in documents
    )

    prompt = (
        "You are a document classification assistant for a receipt/invoice management system. "
        "Given an email's metadata and document previews, identify which documents are actual "
        "financial documents worth keeping.\n\n"
        "Financial documents include: receipts, invoices (incoming or outgoing), flight/travel tickets, "
        "purchase confirmations, financial statements, tax documents, insurance documents, "
        "utility bills, bank statements, or similar transactional documents.\n\n"
        "NOT financial documents: newsletters, marketing materials, app UI screenshots, "
        "terms of service, logos, signatures, banners, general web pages, "
        "duplicate copies of a document already identified.\n\n"
        f"Email sender: {sender_email}\n"
        f"Email subject: {subject}\n"
        f"Email body (truncated):\n{truncated_body}\n\n"
        f"Documents to classify:\n{doc_descriptions}\n\n"
        "Each document's first page is attached as an image (in the same order as listed above).\n\n"
        "Return ONLY a JSON array of identifiers (from the list above) that are real financial documents. "
        "If none qualify, return an empty array. No explanation, just JSON."
    )

    content: list[dict] = [{"type": "text", "text": prompt}]
    for doc in documents:
        import base64
        b64 = base64.b64encode(doc.first_page_image).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    try:
        response = litellm_completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(_strip_code_fences(raw))
        if not isinstance(parsed, list):
            logger.error("LLM returned non-list for document classification: %s", type(parsed))
            return fallback
        # Only return identifiers that were in the input
        return [i for i in parsed if i in all_identifiers]
    except Exception:
        logger.exception("LLM document classification failed, returning all")
        return fallback
```

Move the `import base64` to the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_email_classification.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/ingestion/url_triage.py tests/test_email_classification.py
git commit -m "feat: add classify_email_documents() for smart attachment classification"
```

---

### Task 2: Add `triage_email_urls()` to url_triage.py

This replaces the old `triage_email()` for the URL-only triage step. It receives email context (sender, subject, body) and returns which URLs to fetch.

**Files:**
- Modify: `backend/ingestion/url_triage.py`
- Test: `tests/test_email_classification.py`

- [ ] **Step 1: Add tests for `triage_email_urls()`**

Append to `tests/test_email_classification.py`:

```python
from backend.ingestion.url_triage import triage_email_urls


@pytest.mark.asyncio
async def test_triage_urls_returns_candidates(mock_llm_settings):
    """LLM picks invoice URL from a list."""
    urls = ["https://billing.com/invoice/123", "https://company.com/unsubscribe"]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["https://billing.com/invoice/123"]'

    with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
        result = await triage_email_urls(
            sender_email="billing@vendor.com",
            subject="Your Invoice",
            body_text="Click here to view your invoice.",
            urls=urls,
        )
    assert result == ["https://billing.com/invoice/123"]


@pytest.mark.asyncio
async def test_triage_urls_fallback_on_failure(mock_llm_settings):
    """On LLM failure, returns all URLs."""
    urls = ["https://a.com", "https://b.com"]
    with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("fail")):
        result = await triage_email_urls(
            sender_email="a@b.com", subject="test", body_text="", urls=urls,
        )
    assert set(result) == {"https://a.com", "https://b.com"}


@pytest.mark.asyncio
async def test_triage_urls_empty():
    """No URLs — returns empty list."""
    result = await triage_email_urls(
        sender_email="a@b.com", subject="test", body_text="", urls=[],
    )
    assert result == []
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `uv run pytest tests/test_email_classification.py::test_triage_urls_returns_candidates -v`
Expected: ImportError — `triage_email_urls` doesn't exist yet.

- [ ] **Step 3: Implement `triage_email_urls()`**

Add to `backend/ingestion/url_triage.py`, after `classify_email_documents()`:

```python
async def triage_email_urls(
    sender_email: str,
    subject: str,
    body_text: str,
    urls: list[str],
) -> list[str]:
    """Use LLM to filter URLs that likely point to financial documents.

    Uses email context (sender, subject, body) for better decisions.
    Fallback on LLM failure or missing config: returns ALL URLs.
    """
    if not urls:
        return []

    fallback = list(urls)

    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")

    if not model or not api_key:
        logger.warning("LLM not configured for URL triage, returning all URLs")
        return fallback

    truncated_body = body_text[:3000] if body_text else ""
    url_list = "\n".join(f"- {u}" for u in urls)

    prompt = (
        "You are a document triage assistant for a receipt/invoice management system. "
        "Given an email's metadata and a list of URLs found in the email, determine which URLs "
        "are likely to point to viewable or downloadable financial documents.\n\n"
        "Financial document URLs include: invoice download pages, receipt viewers, "
        "purchase confirmation pages, billing portals with downloadable statements.\n\n"
        "Exclude URLs that are:\n"
        "- Unsubscribe or email preference links\n"
        "- Account management or login pages (unless specifically for viewing an invoice)\n"
        "- Marketing, promotional, or social media links\n"
        "- App store links\n"
        "- Tracking or shipping status links\n"
        "- General company website pages\n"
        "- News, blog, or help articles\n\n"
        f"Email sender: {sender_email}\n"
        f"Email subject: {subject}\n"
        f"Email body (truncated):\n{truncated_body}\n\n"
        f"URLs found in email:\n{url_list}\n\n"
        "Return ONLY a JSON array of URLs to fetch (must be from the provided list). "
        "If none are relevant, return an empty array. No explanation, just JSON."
    )

    try:
        response = litellm_completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(_strip_code_fences(raw))
        if not isinstance(parsed, list):
            logger.error("LLM returned non-list for URL triage: %s", type(parsed))
            return fallback
        return [u for u in parsed if u in urls]
    except Exception:
        logger.exception("LLM URL triage failed, returning all URLs")
        return fallback
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_email_classification.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Remove old `triage_email()` function**

Delete the `triage_email()` function (lines 79-156 of the original file). It is replaced by `triage_email_urls()` + `classify_email_documents()`.

- [ ] **Step 6: Run full test suite to check for breakage**

Run: `uv run pytest tests/ -v`
Expected: Any test importing `triage_email` will fail — that's expected since gmail.py still imports it. We'll fix that in Task 4.

- [ ] **Step 7: Commit**

```bash
git add backend/ingestion/url_triage.py tests/test_email_classification.py
git commit -m "feat: add triage_email_urls() with email context, remove old triage_email()"
```

---

### Task 3: Add desktop User-Agent support to url_fetcher.py

**Files:**
- Modify: `backend/ingestion/url_fetcher.py`

- [ ] **Step 1: Add desktop User-Agent constant**

After the `_MOBILE_USER_AGENT` constant (line 28), add:

```python
_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
```

- [ ] **Step 2: Add `user_agent` parameter to `fetch_url()`**

Change the `fetch_url()` signature (line 228-230) from:

```python
async def fetch_url(
    url: str, download_dir: str | Path, timeout: int = 5
) -> FetchResult | None:
```

to:

```python
async def fetch_url(
    url: str, download_dir: str | Path, timeout: int = 5, user_agent: str | None = None,
) -> FetchResult | None:
```

Replace the hardcoded `_headers` on line 245:

```python
    _headers = {"User-Agent": _MOBILE_USER_AGENT}
```

with:

```python
    ua = user_agent or _MOBILE_USER_AGENT
    _headers = {"User-Agent": ua}
```

Pass the user agent to the HTML link scan (line 278):

```python
        async with httpx.AsyncClient(headers=_headers) as client:
```

This already uses `_headers` so it's correct.

Pass user agent to `_playwright_fetch` (line 285). Change:

```python
        return await _playwright_fetch(url, download_dir, timeout)
```

to:

```python
        return await _playwright_fetch(url, download_dir, timeout, user_agent=ua)
```

- [ ] **Step 3: Add `user_agent` parameter to `_playwright_fetch()`**

Change the signature (line 158-160) from:

```python
async def _playwright_fetch(
    url: str, download_dir: str, timeout: int
) -> FetchResult | None:
```

to:

```python
async def _playwright_fetch(
    url: str, download_dir: str, timeout: int, user_agent: str | None = None,
) -> FetchResult | None:
```

Replace the hardcoded mobile settings in `browser.new_page()` (lines 174-178):

```python
                page = await browser.new_page(
                    user_agent=_MOBILE_USER_AGENT,
                    viewport={"width": 412, "height": 915},
                    is_mobile=True,
                )
```

with:

```python
                ua = user_agent or _MOBILE_USER_AGENT
                is_mobile = ua == _MOBILE_USER_AGENT
                viewport = {"width": 412, "height": 915} if is_mobile else {"width": 1280, "height": 900}
                page = await browser.new_page(
                    user_agent=ua,
                    viewport=viewport,
                    is_mobile=is_mobile,
                )
```

Also update the httpx client inside `_playwright_fetch` (line 201):

```python
                async with httpx.AsyncClient(headers={"User-Agent": _MOBILE_USER_AGENT}) as client:
```

to:

```python
                async with httpx.AsyncClient(headers={"User-Agent": ua}) as client:
```

- [ ] **Step 4: Commit**

```bash
git add backend/ingestion/url_fetcher.py
git commit -m "feat: add user_agent parameter to fetch_url() for desktop/mobile mode"
```

---

### Task 4: Rewrite `_process_message()` in gmail.py

**Files:**
- Modify: `backend/ingestion/gmail.py`
- Test: `tests/test_gmail_smart_ingestion.py`

- [ ] **Step 1: Write tests for the two-phase flow**

Create `tests/test_gmail_smart_ingestion.py`:

```python
"""Tests for the smart two-phase email ingestion pipeline."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from backend.ingestion.gmail import _process_message_logic


def _make_email_context(attachments=None, urls=None, sender="test@vendor.com",
                        subject="Invoice", body="Your invoice is attached."):
    return {
        "sender_email": sender,
        "subject": subject,
        "body_text": body,
        "html_body": f"<p>{body}</p>",
        "attachments": attachments or [],
        "urls": urls or [],
        "authorized": True,
    }


def _make_attachment(filename="invoice.pdf", content_type="application/pdf",
                     content=b"%PDF-1.4 fake pdf content"):
    return {
        "filename": filename,
        "content_type": content_type,
        "size": len(content),
        "content": content,
    }


class TestPhase1AttachmentClassification:
    """Phase 1: When email has attachments, classify them first."""

    def test_attachment_classified_as_financial_skips_urls(self, tmp_path):
        """If attachment is a financial doc, ingest it and skip URLs."""
        ctx = _make_email_context(
            attachments=[_make_attachment()],
            urls=["https://vendor.com/invoice/view"],
        )
        with patch("backend.ingestion.gmail._classify_attachments", return_value=["invoice.pdf"]) as mock_classify, \
             patch("backend.ingestion.gmail._ingest_attachment", return_value={"status": "ingested", "doc_id": 1}) as mock_ingest, \
             patch("backend.ingestion.gmail._process_urls") as mock_urls:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_classify.assert_called_once()
            mock_ingest.assert_called_once()
            mock_urls.assert_not_called()
            assert len(result["ingested"]) == 1

    def test_no_attachment_qualifies_falls_through_to_urls(self, tmp_path):
        """If no attachment qualifies, proceed to URL processing."""
        ctx = _make_email_context(
            attachments=[_make_attachment(filename="logo.png", content_type="image/png", content=b"tiny")],
            urls=["https://vendor.com/invoice/view"],
        )
        with patch("backend.ingestion.gmail._classify_attachments", return_value=[]) as mock_classify, \
             patch("backend.ingestion.gmail._process_urls", return_value=[{"status": "ingested", "doc_id": 2}]) as mock_urls:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_classify.assert_called_once()
            mock_urls.assert_called_once()
            assert len(result["ingested"]) == 1


class TestPhase2URLFallback:
    """Phase 2: URL processing when no attachment qualifies."""

    def test_urls_only_email_processes_urls(self, tmp_path):
        """Email with only URLs goes directly to URL processing."""
        ctx = _make_email_context(
            attachments=[],
            urls=["https://vendor.com/invoice/123"],
        )
        with patch("backend.ingestion.gmail._process_urls", return_value=[{"status": "ingested", "doc_id": 3}]) as mock_urls:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_urls.assert_called_once()
            assert len(result["ingested"]) == 1


class TestNothingFound:
    """Notification when no documents are ingested."""

    def test_nothing_found_notification_sent(self, tmp_path):
        """When no attachments or URLs produce documents, notify."""
        ctx = _make_email_context(attachments=[], urls=[])
        with patch("backend.ingestion.gmail._notify_nothing_found") as mock_notify:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_notify.assert_called_once_with(
                sender_email="test@vendor.com",
                subject="Invoice",
                attachment_count=0,
                url_count=0,
            )

    def test_nothing_found_when_urls_produce_no_docs(self, tmp_path):
        """URLs were checked but nothing qualified."""
        ctx = _make_email_context(
            attachments=[],
            urls=["https://marketing.com/promo"],
        )
        with patch("backend.ingestion.gmail._process_urls", return_value=[]) as mock_urls, \
             patch("backend.ingestion.gmail._notify_nothing_found") as mock_notify:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_notify.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmail_smart_ingestion.py -v`
Expected: ImportError — `_process_message_logic` doesn't exist yet.

- [ ] **Step 3: Add `_render_first_page()` helper to gmail.py**

Add after the `_run_async()` function (after line 164):

```python
def _render_first_page(content: bytes, filename: str, data_dir: str) -> bytes | None:
    """Render the first page of a document to PNG for LLM classification.

    Handles PDFs directly and converts images to PDF first.
    Returns PNG bytes or None on failure.
    """
    import tempfile
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            from backend.processing.normalize import normalize_file
            norm = normalize_file(tmp_path, data_dir)
            pdf_path = norm.pdf_path
            from backend.storage import render_all_pages_to_memory
            pages = render_all_pages_to_memory(pdf_path, dpi=150)
            if norm.converted and os.path.exists(pdf_path):
                os.unlink(pdf_path)
            return pages[0] if pages else None
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception as e:
        logger.debug(f"Failed to render first page of {filename}: {e}")
        return None
```

- [ ] **Step 4: Add `_classify_attachments()` helper**

Add after `_render_first_page()`:

```python
def _classify_attachments(
    attachments: list[dict], sender_email: str, subject: str, body_text: str, data_dir: str,
) -> list[str]:
    """Render attachment first pages and classify via LLM. Returns list of qualifying filenames."""
    from backend.ingestion.url_triage import classify_email_documents, ClassificationDocument

    docs = []
    for att in attachments:
        image = _render_first_page(att["content"], att["filename"], data_dir)
        if image is None:
            continue
        docs.append(ClassificationDocument(
            identifier=att["filename"],
            source="attachment",
            first_page_image=image,
        ))

    if not docs:
        return []

    return _run_async(classify_email_documents(
        sender_email=sender_email,
        subject=subject,
        body_text=body_text,
        documents=docs,
    ))
```

- [ ] **Step 5: Add `_process_urls()` helper**

Add after `_classify_attachments()`:

```python
def _process_urls(
    urls: list[str], sender_email: str, subject: str, body_text: str,
    data_dir: str, authorized: bool,
) -> list[dict]:
    """Phase 2: Triage URLs, fetch candidates, classify fetched docs, ingest qualifying ones."""
    from backend.ingestion.url_triage import triage_email_urls, classify_email_documents, ClassificationDocument
    from backend.ingestion.url_fetcher import _DESKTOP_USER_AGENT

    # Step 2a: LLM triage to filter candidate URLs
    candidate_urls = _run_async(triage_email_urls(
        sender_email=sender_email,
        subject=subject,
        body_text=body_text,
        urls=urls,
    ))

    if not candidate_urls:
        return []

    # Step 2b: Fetch candidate URLs (desktop mode for email-sourced URLs)
    download_dir = os.path.join(data_dir, "storage", "tmp")
    os.makedirs(download_dir, exist_ok=True)

    fetched = []  # list of (url, fetch_result)
    for url in candidate_urls:
        result = _run_async(fetch_url(url, download_dir, user_agent=_DESKTOP_USER_AGENT))
        if result is not None:
            fetched.append((url, result))

    if not fetched:
        return []

    # Step 2c: Classify fetched documents
    docs = []
    for url, fr in fetched:
        image = _render_first_page_from_file(fr.file_path, data_dir)
        if image is not None:
            docs.append(ClassificationDocument(
                identifier=url,
                source="url",
                first_page_image=image,
            ))

    qualifying_urls = set()
    if docs:
        qualifying_urls = set(_run_async(classify_email_documents(
            sender_email=sender_email,
            subject=subject,
            body_text=body_text,
            documents=docs,
        )))

    # Ingest qualifying, clean up non-qualifying
    ingested = []
    for url, fr in fetched:
        if url in qualifying_urls:
            result = _ingest_url(url, sender_email, data_dir, authorized, fetch_result=fr)
            ingested.append(result)
        else:
            # Clean up non-qualifying fetched file
            if os.path.exists(fr.file_path):
                os.unlink(fr.file_path)
            logger.info(f"Email: discarded non-qualifying URL {url}")

    return ingested
```

- [ ] **Step 6: Add `_render_first_page_from_file()` helper**

Add after `_render_first_page()`:

```python
def _render_first_page_from_file(file_path: str, data_dir: str) -> bytes | None:
    """Render first page of an already-fetched file to PNG."""
    try:
        from backend.processing.normalize import normalize_file
        norm = normalize_file(file_path, data_dir)
        pdf_path = norm.pdf_path
        from backend.storage import render_all_pages_to_memory
        pages = render_all_pages_to_memory(pdf_path, dpi=150)
        if norm.converted and os.path.exists(pdf_path):
            os.unlink(pdf_path)
        return pages[0] if pages else None
    except Exception as e:
        logger.debug(f"Failed to render first page of {file_path}: {e}")
        return None
```

- [ ] **Step 7: Add `_notify_nothing_found()` helper**

Add after `_process_urls()`:

```python
def _notify_nothing_found(sender_email: str, subject: str, attachment_count: int, url_count: int) -> None:
    """Send notification when an email produced no ingested documents."""
    try:
        from backend.notifications.notifier import notify
        notify("nothing_found", {
            "sender_email": sender_email,
            "subject": subject,
            "attachment_count": attachment_count,
            "url_count": url_count,
        })
    except Exception:
        pass
```

- [ ] **Step 8: Implement `_process_message_logic()` — the core two-phase flow**

Add after `_notify_nothing_found()`:

```python
def _process_message_logic(ctx: dict, data_dir: str) -> dict:
    """Core two-phase ingestion logic, separated from IMAP parsing for testability.

    ctx keys: sender_email, subject, body_text, html_body, attachments, urls, authorized
    """
    sender_email = ctx["sender_email"]
    subject = ctx["subject"]
    body_text = ctx["body_text"]
    attachments = ctx["attachments"]
    urls = ctx["urls"]
    authorized = ctx["authorized"]

    ingested = []

    # Phase 1: Classify attachments (if any)
    if attachments:
        qualifying_filenames = _classify_attachments(
            attachments, sender_email, subject, body_text, data_dir,
        )

        if qualifying_filenames:
            # Ingest qualifying attachments, skip URLs
            att_by_name = {a["filename"]: a for a in attachments}
            for fname in qualifying_filenames:
                if fname in att_by_name:
                    att = att_by_name[fname]
                    result = _ingest_attachment(
                        att["content"], att["filename"], sender_email, data_dir, authorized,
                    )
                    ingested.append(result)

            return {"ingested": ingested}

    # Phase 2: URL fallback (no attachment qualified, or no attachments)
    if urls:
        url_results = _process_urls(
            urls, sender_email, subject, body_text, data_dir, authorized,
        )
        ingested.extend(url_results)

    # Phase 3: Notify if nothing was ingested
    if not ingested:
        _notify_nothing_found(
            sender_email=sender_email,
            subject=subject,
            attachment_count=len(attachments),
            url_count=len(urls),
        )

    return {"ingested": ingested}
```

- [ ] **Step 9: Update `_process_message()` to use `_process_message_logic()`**

Replace the body of `_process_message()` (lines 245-343) with:

```python
def _process_message(mail: imaplib.IMAP4_SSL, msg_id: bytes, data_dir: str) -> dict:
    """Fetch and process a single email message using two-phase smart ingestion."""
    status, msg_data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"msg_id": msg_id.decode(), "status": "error", "error": "Failed to fetch message"}

    raw_bytes = msg_data[0][1]
    parsed = email.message_from_bytes(raw_bytes, policy=email_policy.default)

    from_header = str(parsed.get("From", ""))
    subject = str(parsed.get("Subject", "(no subject)"))
    sender_email = _extract_sender_email(from_header)
    authorized = _is_sender_authorized(sender_email)

    # Collect attachments and URLs
    attachments = _collect_attachments(parsed)

    html_body = ""
    text_body = ""
    for part in parsed.walk():
        ct = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if ct == "text/html" and not html_body:
            html_body = payload.decode("utf-8", errors="replace")
        elif ct == "text/plain" and not text_body:
            text_body = payload.decode("utf-8", errors="replace")

    urls = _extract_urls_from_html(html_body) if html_body else []
    if text_body:
        text_urls = _extract_urls_from_text(text_body)
        seen = set(urls)
        urls.extend(u for u in text_urls if u not in seen)

    ctx = {
        "sender_email": sender_email,
        "subject": subject,
        "body_text": text_body or html_body,
        "html_body": html_body,
        "attachments": attachments,
        "urls": urls,
        "authorized": authorized,
    }

    result = _process_message_logic(ctx, data_dir)

    # Mark as seen
    mail.store(msg_id, "+FLAGS", "\\Seen")

    return {
        "msg_id": msg_id.decode(),
        "subject": subject,
        "sender": sender_email,
        "authorized": authorized,
        **result,
    }
```

- [ ] **Step 10: Update `_ingest_url()` to accept optional pre-fetched result**

Change the signature of `_ingest_url()` (line 167) from:

```python
def _ingest_url(url: str, sender_email: str, data_dir: str, authorized: bool) -> dict:
```

to:

```python
def _ingest_url(url: str, sender_email: str, data_dir: str, authorized: bool, fetch_result=None) -> dict:
```

At the top of the function body, replace lines 169-173:

```python
    download_dir = os.path.join(data_dir, "storage", "tmp")
    os.makedirs(download_dir, exist_ok=True)

    fetch_result = _run_async(fetch_url(url, download_dir))
    if fetch_result is None:
        return {"url": url, "status": "fetch_failed"}
```

with:

```python
    if fetch_result is None:
        download_dir = os.path.join(data_dir, "storage", "tmp")
        os.makedirs(download_dir, exist_ok=True)
        fetch_result = _run_async(fetch_url(url, download_dir))
        if fetch_result is None:
            return {"url": url, "status": "fetch_failed"}
```

- [ ] **Step 11: Update imports in gmail.py**

Replace the import of `triage_email` (line 16):

```python
from backend.ingestion.url_triage import triage_email
```

with:

```python
# triage imports are done locally in _classify_attachments() and _process_urls()
```

(Remove the line entirely — triage functions are imported locally within the helper functions.)

- [ ] **Step 12: Run tests**

Run: `uv run pytest tests/test_gmail_smart_ingestion.py tests/test_email_classification.py -v`
Expected: All tests PASS.

- [ ] **Step 13: Commit**

```bash
git add backend/ingestion/gmail.py tests/test_gmail_smart_ingestion.py
git commit -m "feat: rewrite email ingestion with two-phase attachments-first pipeline"
```

---

### Task 5: Add "nothing_found" notification

**Files:**
- Modify: `backend/notifications/templates.py`
- Modify: `backend/notifications/notifier.py`
- Modify: `backend/config.py`

- [ ] **Step 1: Add `format_nothing_found()` to templates.py**

Add at the end of `backend/notifications/templates.py`:

```python
def format_nothing_found(info: dict, base_url: str) -> dict:
    sender = info.get("sender_email", "unknown")
    subject = info.get("subject", "(no subject)")
    att_count = info.get("attachment_count", 0)
    url_count = info.get("url_count", 0)

    subject_line = f"Receiptory: No documents found \u2014 {subject}"

    checked_parts = []
    if att_count:
        checked_parts.append(f"{att_count} attachment(s)")
    if url_count:
        checked_parts.append(f"{url_count} URL(s)")
    checked = ", ".join(checked_parts) if checked_parts else "nothing"

    caption_parts = [
        "\U0001f50d <b>No Documents Found</b>",
        f"From: {sender}",
        f"Subject: {subject}",
        f"Checked: {checked}",
        "No receipts or invoices were identified.",
    ]
    caption = "\n".join(caption_parts)

    html_parts = [
        "<h3>&#128269; No Documents Found</h3>",
        "<p>",
        f"<b>From:</b> {sender}<br>",
        f"<b>Subject:</b> {subject}<br>",
        f"<b>Checked:</b> {checked}<br>",
        "No receipts or invoices were identified in this email.",
        "</p>",
    ]
    html = "\n".join(html_parts)

    return {"subject": subject_line, "caption": caption, "html": html}
```

- [ ] **Step 2: Register in notifier.py**

In `backend/notifications/notifier.py`, add to the import (line 4-7):

```python
from backend.notifications.templates import (
    format_ingested, format_processed, format_failed,
    format_needs_review, format_backup_ok, format_backup_failed,
    format_nothing_found,
)
```

Add to the `FORMATTERS` dict (after line 18):

```python
    "nothing_found": format_nothing_found,
```

- [ ] **Step 3: Add notification settings to config.py**

In `backend/config.py`, add to `DEFAULTS` dict (after the existing `notify_email_backup_failed` entry, around line 71):

```python
    "notify_telegram_nothing_found": True,
    "notify_email_nothing_found": False,
```

- [ ] **Step 4: Commit**

```bash
git add backend/notifications/templates.py backend/notifications/notifier.py backend/config.py
git commit -m "feat: add nothing_found notification for emails with no qualifying documents"
```

---

### Task 6: Integration test and cleanup

**Files:**
- Test: `tests/test_email_classification.py`

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests pass. If any existing tests break due to removed `triage_email` import, fix them.

- [ ] **Step 2: Check that no other code imports the removed `triage_email` function**

Search the codebase:

```bash
grep -r "triage_email" backend/ --include="*.py"
```

Expected: Only `triage_email_urls` and `triage_telegram_urls` references. If `triage_email` is still referenced anywhere, update those references.

- [ ] **Step 3: Run full test suite again**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: clean up remaining triage_email references"
```

(Skip this commit if no changes were needed.)
