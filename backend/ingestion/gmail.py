import asyncio as _asyncio
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
from backend.ingestion.url_triage import triage_email, triage_telegram_urls
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
    urls = []
    _exclude_re = re.compile(r"unsubscribe|mailto:|tel:", re.IGNORECASE)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if _exclude_re.search(href):
            continue
        if href.startswith("http://") or href.startswith("https://"):
            urls.append(href)
    return urls


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
        return _asyncio.run(coro)
    except RuntimeError:
        # Already in an event loop — use a thread pool executor
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_asyncio.run, coro).result()


def _ingest_url(url: str, sender_email: str, data_dir: str, authorized: bool) -> dict:
    """Fetch a URL and ingest it as a document, similar to _ingest_attachment."""
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


def _process_message(mail: imaplib.IMAP4_SSL, msg_id: bytes, data_dir: str) -> dict:
    """Fetch and process a single email message using triage-based flow."""
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
    for part in parsed.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                html_body = payload.decode("utf-8", errors="replace")
                break

    urls = _extract_urls_from_html(html_body) if html_body else []

    ingested = []
    has_attachments = len(attachments) > 0
    has_urls = len(urls) > 0

    if has_attachments and has_urls:
        # LLM triage decides what to ingest
        triage_input = [
            {"filename": a["filename"], "content_type": a["content_type"], "size": a["size"]}
            for a in attachments
        ]
        triage_result = _run_async(triage_email(html_body, triage_input, urls))

        # Ingest recommended attachments
        att_by_name = {a["filename"]: a for a in attachments}
        for fname in triage_result.ingest_attachments:
            if fname in att_by_name:
                att = att_by_name[fname]
                result = _ingest_attachment(att["content"], att["filename"], sender_email, data_dir, authorized)
                ingested.append(result)

        # Ingest recommended URLs
        for url in triage_result.ingest_urls:
            result = _ingest_url(url, sender_email, data_dir, authorized)
            ingested.append(result)

    elif has_attachments and not has_urls:
        # Ingest all attachments (original behavior)
        for att in attachments:
            result = _ingest_attachment(att["content"], att["filename"], sender_email, data_dir, authorized)
            ingested.append(result)

    elif not has_attachments and has_urls:
        # LLM triage on URLs only
        triage_result = _run_async(triage_email(html_body, [], urls))

        for url in triage_result.ingest_urls:
            result = _ingest_url(url, sender_email, data_dir, authorized)
            ingested.append(result)

    else:
        # No attachments, no URLs — create needs_review document
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (original_filename, file_hash, file_size_bytes, status, submission_channel,
                    sender_identifier, user_notes)
                   VALUES (?, ?, 0, 'needs_review', 'email', ?, ?)""",
                (f"email_{msg_id.decode()}.eml", f"no-content-{msg_id.decode()}",
                 f"email:{sender_email}", f"Email with no attachments or URLs. Subject: {subject}"),
            )
            doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        ingested.append({"status": "needs_review", "doc_id": doc_id})

    # Mark as seen
    mail.store(msg_id, "+FLAGS", "\\Seen")

    return {
        "msg_id": msg_id.decode(),
        "subject": subject,
        "sender": sender_email,
        "authorized": authorized,
        "ingested": ingested,
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
