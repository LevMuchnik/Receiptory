import asyncio
import concurrent.futures
import email
import imaplib
import logging
import os
import re
import tempfile
from email import policy as email_policy

from bs4 import BeautifulSoup as _BS

from backend.config import get_setting
from backend.database import get_connection
from backend.ingestion.url_fetcher import fetch_url
from backend.storage import compute_file_hash, save_original

logger = logging.getLogger(__name__)


def _connect_imap():
    """Connect to IMAP server with configured credentials."""
    address = get_setting("gmail_address")
    app_password = get_setting("gmail_app_password")
    if not address or not app_password:
        return None

    host = get_setting("gmail_imap_host")
    port = get_setting("gmail_imap_port")

    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(address, app_password)
    return mail


def test_connection() -> dict:
    """Test IMAP connection. Returns status dict."""
    address = get_setting("gmail_address")
    app_password = get_setting("gmail_app_password")
    if not address or not app_password:
        return {"status": "not_configured", "message": "Email address and App Password required"}

    labels = get_setting("gmail_labels")
    if not labels:
        return {"status": "not_configured", "message": "No labels configured — email ingestion is disabled"}

    host = get_setting("gmail_imap_host")
    port = get_setting("gmail_imap_port")

    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(address, app_password)

        unread_only = get_setting("gmail_unread_only")
        total_matching = 0
        for label in labels:
            try:
                status, _ = mail.select(f'"{label}"', readonly=True)
                if status == "OK":
                    criteria = "UNSEEN" if unread_only else "ALL"
                    s, data = mail.search(None, criteria)
                    if s == "OK" and data[0]:
                        total_matching += len(data[0].split())
            except Exception:
                pass

        mail.logout()
        return {
            "status": "connected",
            "email": address,
            "host": host,
            "labels": labels,
            "unread_only": unread_only,
            "matching": total_matching,
        }
    except imaplib.IMAP4.error as e:
        return {"status": "error", "message": f"IMAP login failed: {e}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _is_sender_authorized(sender_email: str) -> bool:
    """Check if a sender is in the authorized senders list.
    Supports exact emails and domain rules (e.g., '@example.com')."""
    authorized = get_setting("gmail_authorized_senders")
    if not authorized:
        return True

    sender_lower = sender_email.lower()
    for rule in authorized:
        rule_lower = rule.strip().lower()
        if rule_lower.startswith("@"):
            if sender_lower.endswith(rule_lower):
                return True
        else:
            if sender_lower == rule_lower:
                return True
    return False


def _extract_sender_email(from_header: str) -> str:
    """Extract email address from a From header like 'Name <email@example.com>'."""
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1)
    return from_header.strip()


def _extract_urls_from_html(html: str) -> list[str]:
    """Extract HTTP(S) URLs from HTML email body, excluding unsubscribe/mailto/tel links."""
    soup = _BS(html, "html.parser")
    seen = set()
    urls = []
    _exclude_re = re.compile(r"unsubscribe|mailto:|tel:", re.IGNORECASE)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if _exclude_re.search(href):
            continue
        if href.startswith("http://") or href.startswith("https://"):
            if href not in seen:
                seen.add(href)
                urls.append(href)
    return urls


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract HTTP(S) URLs from plain text email body."""
    urls = re.findall(r"https?://[^\s<>\"']+", text)
    seen = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def _collect_attachments(parsed) -> list[dict]:
    """Walk MIME parts, return list of dicts with filename, content_type, size, content (bytes)."""
    attachments = []
    for part in parsed.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))

        if "attachment" in content_disposition or content_type in (
            "application/pdf", "image/jpeg", "image/png", "image/tiff"
        ):
            filename = part.get_filename() or "attachment"
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            attachments.append({
                "filename": filename,
                "content_type": content_type,
                "size": len(payload),
                "content": payload,
            })
    return attachments


def _run_async(coro):
    """Run an async coroutine from sync context, handling existing event loops."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Already in an event loop — use a thread pool executor
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


def _render_first_page(content: bytes, filename: str, data_dir: str) -> bytes | None:
    """Render the first page of a document to PNG for LLM classification."""
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
            if os.path.exists(fr.file_path):
                os.unlink(fr.file_path)
            logger.info(f"Email: discarded non-qualifying URL {url}")

    return ingested


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


def _ingest_url(url: str, sender_email: str, data_dir: str, authorized: bool, fetch_result=None) -> dict:
    """Fetch a URL and ingest it as a document, similar to _ingest_attachment."""
    if fetch_result is None:
        download_dir = os.path.join(data_dir, "storage", "tmp")
        os.makedirs(download_dir, exist_ok=True)
        fetch_result = _run_async(fetch_url(url, download_dir))
        if fetch_result is None:
            return {"url": url, "status": "fetch_failed"}

    try:
        file_hash = compute_file_hash(fetch_result.file_path)
        file_size = os.path.getsize(fetch_result.file_path)

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()

        if existing:
            os.unlink(fetch_result.file_path)
            return {"url": url, "status": "duplicate", "existing_id": existing["id"]}

        ext = os.path.splitext(fetch_result.file_path)[1] or ".bin"
        save_original(fetch_result.file_path, file_hash, ext, data_dir)

        category_id = None
        status = "pending" if authorized else "needs_review"
        if fetch_result.auth_wall:
            status = "needs_review"
        if not authorized:
            with get_connection() as conn:
                cat = conn.execute(
                    "SELECT id FROM categories WHERE name = 'unauthorized_sender' AND is_system = 1"
                ).fetchone()
                if cat:
                    category_id = cat["id"]

        user_notes = None
        if fetch_result.auth_wall:
            user_notes = f"Auth-gated URL: {url}"

        filename = os.path.basename(fetch_result.file_path)
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (original_filename, file_hash, file_size_bytes, status, submission_channel,
                    sender_identifier, category_id, source_url, user_notes)
                   VALUES (?, ?, ?, ?, 'email', ?, ?, ?, ?)""",
                (filename, file_hash, file_size, status,
                 f"email:{sender_email}", category_id, url, user_notes),
            )
            doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        if os.path.exists(fetch_result.file_path):
            os.unlink(fetch_result.file_path)

        logger.info(f"Email: ingested URL {url} as document #{doc_id} from {sender_email}")
        try:
            from backend.notifications.notifier import notify
            notify("ingested", {
                "id": doc_id,
                "original_filename": filename,
                "file_hash": file_hash,
                "submission_channel": "email",
                "sender_identifier": f"email:{sender_email}",
                "source_url": url,
            })
        except Exception:
            pass
        return {"url": url, "status": "ingested", "doc_id": doc_id, "authorized": authorized}

    except Exception as e:
        if os.path.exists(fetch_result.file_path):
            os.unlink(fetch_result.file_path)
        logger.error(f"Email: failed to ingest URL {url}: {e}")
        return {"url": url, "status": "error", "error": str(e)}


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

    mail.store(msg_id, "+FLAGS", "\\Seen")

    return {
        "msg_id": msg_id.decode(),
        "subject": subject,
        "sender": sender_email,
        "authorized": authorized,
        **result,
    }


def _ingest_attachment(content: bytes, filename: str, sender_email: str, data_dir: str, authorized: bool) -> dict:
    """Save an attachment and create a document record."""
    ext = os.path.splitext(filename)[1].lower() or ".bin"

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        file_hash = compute_file_hash(tmp_path)
        file_size = len(content)

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()

        if existing:
            os.unlink(tmp_path)
            return {"filename": filename, "status": "duplicate", "existing_id": existing["id"]}

        save_original(tmp_path, file_hash, ext, data_dir)

        category_id = None
        if not authorized:
            with get_connection() as conn:
                cat = conn.execute(
                    "SELECT id FROM categories WHERE name = 'unauthorized_sender' AND is_system = 1"
                ).fetchone()
                if cat:
                    category_id = cat["id"]

        with get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (original_filename, file_hash, file_size_bytes, status, submission_channel,
                    sender_identifier, category_id)
                   VALUES (?, ?, ?, ?, 'email', ?, ?)""",
                (filename, file_hash, file_size,
                 "pending" if authorized else "needs_review",
                 f"email:{sender_email}", category_id),
            )
            doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        os.unlink(tmp_path)
        logger.info(f"Email: ingested {filename} as document #{doc_id} from {sender_email} (authorized={authorized})")
        try:
            from backend.notifications.notifier import notify
            notify("ingested", {
                "id": doc_id,
                "original_filename": filename,
                "file_hash": file_hash,
                "submission_channel": "email",
                "sender_identifier": f"email:{sender_email}",
            })
        except Exception:
            pass
        return {"filename": filename, "status": "ingested", "doc_id": doc_id, "authorized": authorized}

    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        logger.error(f"Email: failed to ingest {filename}: {e}")
        return {"filename": filename, "status": "error", "error": str(e)}


def poll_gmail(data_dir: str) -> list[dict]:
    """Poll configured IMAP labels for unread messages and process them."""
    labels = get_setting("gmail_labels")
    if not labels:
        return []

    mail = _connect_imap()
    if mail is None:
        return []

    try:
        processed = []
        unread_only = get_setting("gmail_unread_only")
        criteria = "UNSEEN" if unread_only else "ALL"

        for label in labels:
            try:
                status, _ = mail.select(f'"{label}"')
                if status != "OK":
                    logger.warning(f"Email: could not select label '{label}'")
                    continue

                status, data = mail.search(None, criteria)
                if status != "OK" or not data[0]:
                    continue

                msg_ids = data[0].split()
                logger.info(f"Email: found {len(msg_ids)} {'unread ' if unread_only else ''}message(s) in '{label}'")

                for msg_id in msg_ids[:20]:
                    try:
                        result = _process_message(mail, msg_id, data_dir)
                        result["label"] = label
                        processed.append(result)
                    except Exception as e:
                        logger.error(f"Email: failed to process message {msg_id} in '{label}': {e}")
                        processed.append({"msg_id": msg_id.decode(), "label": label, "status": "error", "error": str(e)})
            except Exception as e:
                logger.error(f"Email: error processing label '{label}': {e}")

        mail.logout()
        return processed

    except Exception as e:
        logger.error(f"Email poll failed: {e}")
        try:
            mail.logout()
        except Exception:
            pass
        return []


async def run_gmail_poller(data_dir: str) -> None:
    """Background loop that polls email on a configurable interval."""
    logger.info("Email poller started")

    while True:
        try:
            address = get_setting("gmail_address")
            app_password = get_setting("gmail_app_password")
            labels = get_setting("gmail_labels")
            if not address or not app_password or not labels:
                await asyncio.sleep(60)
                continue

            poll_interval = get_setting("gmail_poll_interval")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, poll_gmail, data_dir)
            await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            logger.info("Email poller shutting down")
            break
        except Exception as e:
            logger.error(f"Email poller error: {e}")
            await asyncio.sleep(60)
