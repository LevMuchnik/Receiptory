"""Notification content templates for all event types."""


def _doc_link(base_url: str, doc_id: int) -> str:
    if base_url and doc_id:
        return f"{base_url.rstrip('/')}/documents/{doc_id}"
    return ""


def format_ingested(doc: dict, base_url: str) -> dict:
    doc_id = doc.get("id", "")
    filename = doc.get("original_filename", "unknown")
    channel = doc.get("submission_channel", "unknown")
    sender = doc.get("sender_identifier", "")
    link = _doc_link(base_url, doc_id)

    channel_line = channel
    if sender:
        channel_line = f"{channel} (from {sender})"

    subject = f"Receiptory: New document \u2014 {filename}"

    caption_parts = [
        "\U0001f4e5 <b>Document Ingested</b>",
        f"File: {filename}",
        f"Channel: {channel_line}",
        "Status: Queued for processing",
    ]
    if link:
        caption_parts.append(f"\U0001f517 {link}")
    caption = "\n".join(caption_parts)

    html_parts = [
        "<h3>&#128229; Document Ingested</h3>",
        f"<p><b>File:</b> {filename}<br>",
        f"<b>Channel:</b> {channel_line}<br>",
        "<b>Status:</b> Queued for processing</p>",
    ]
    if link:
        html_parts.append(f'<p><a href="{link}">View document</a></p>')
    html = "\n".join(html_parts)

    return {"subject": subject, "caption": caption, "html": html}


def format_processed(doc: dict, base_url: str) -> dict:
    doc_id = doc.get("id", "")
    filename = doc.get("original_filename", "unknown")
    vendor = doc.get("vendor_name") or "Unknown"
    receipt_date = doc.get("receipt_date") or "Unknown"
    total = doc.get("total_amount")
    currency = doc.get("currency") or ""
    category = doc.get("category_name") or "uncategorized"
    confidence = doc.get("extraction_confidence")
    link = _doc_link(base_url, doc_id)

    amount_str = f"{currency}{total:.2f}" if total is not None else "N/A"
    conf_str = f"{int(confidence * 100)}%" if confidence is not None else "N/A"

    subject = f"Receiptory: Processed \u2014 {vendor} {amount_str}"

    caption_parts = [
        "\u2705 <b>Document Processed</b>",
        f"Vendor: {vendor}",
        f"Date: {receipt_date} | Amount: {amount_str}",
        f"Category: {category}",
        f"Confidence: {conf_str}",
    ]
    if link:
        caption_parts.append(f"\U0001f517 {link}")
    caption = "\n".join(caption_parts)

    html_parts = [
        "<h3>&#9989; Document Processed</h3>",
        "<p>",
        f"<b>Vendor:</b> {vendor}<br>",
        f"<b>Date:</b> {receipt_date} | <b>Amount:</b> {amount_str}<br>",
        f"<b>Category:</b> {category}<br>",
        f"<b>Confidence:</b> {conf_str}",
        "</p>",
    ]
    if link:
        html_parts.append(f'<p><a href="{link}">View document</a></p>')
    html = "\n".join(html_parts)

    return {"subject": subject, "caption": caption, "html": html}


def format_failed(doc: dict, base_url: str) -> dict:
    doc_id = doc.get("id", "")
    filename = doc.get("original_filename", "unknown")
    error = doc.get("processing_error") or "Unknown error"
    attempts = doc.get("processing_attempts", "?")
    link = _doc_link(base_url, doc_id)

    subject = f"Receiptory: Processing failed \u2014 {filename}"

    caption_parts = [
        "\u274c <b>Processing Failed</b>",
        f"File: {filename}",
        f"Error: {error}",
        f"Attempts: {attempts}",
    ]
    if link:
        caption_parts.append(f"\U0001f517 {link}")
    caption = "\n".join(caption_parts)

    html_parts = [
        "<h3>&#10060; Processing Failed</h3>",
        "<p>",
        f"<b>File:</b> {filename}<br>",
        f"<b>Error:</b> {error}<br>",
        f"<b>Attempts:</b> {attempts}",
        "</p>",
    ]
    if link:
        html_parts.append(f'<p><a href="{link}">View document</a></p>')
    html = "\n".join(html_parts)

    return {"subject": subject, "caption": caption, "html": html}


def format_needs_review(doc: dict, base_url: str) -> dict:
    doc_id = doc.get("id", "")
    filename = doc.get("original_filename", "unknown")
    vendor = doc.get("vendor_name") or "Unknown"
    total = doc.get("total_amount")
    currency = doc.get("currency") or ""
    confidence = doc.get("extraction_confidence")
    link = _doc_link(base_url, doc_id)

    amount_str = f"{currency}{total:.2f}" if total is not None else "0.00"
    conf_str = f"{int(confidence * 100)}%" if confidence is not None else "N/A"

    subject = f"Receiptory: Review needed \u2014 {filename} ({conf_str})"

    caption_parts = [
        "\u26a0\ufe0f <b>Needs Review</b>",
        f"File: {filename}",
        f"Vendor: {vendor} | Amount: {amount_str}",
        f"Confidence: {conf_str}",
    ]
    if link:
        caption_parts.append(f"\U0001f517 {link}")
    caption = "\n".join(caption_parts)

    html_parts = [
        "<h3>&#9888;&#65039; Needs Review</h3>",
        "<p>",
        f"<b>File:</b> {filename}<br>",
        f"<b>Vendor:</b> {vendor} | <b>Amount:</b> {amount_str}<br>",
        f"<b>Confidence:</b> {conf_str}",
        "</p>",
    ]
    if link:
        html_parts.append(f'<p><a href="{link}">View document</a></p>')
    html = "\n".join(html_parts)

    return {"subject": subject, "caption": caption, "html": html}


def format_backup_ok(backup_info: dict) -> dict:
    backup_type = backup_info.get("backup_type", "unknown")
    size_bytes = backup_info.get("size_bytes", 0)
    destination = backup_info.get("destination", "unknown")

    size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
    size_str = f"{size_mb:.0f} MB" if size_mb >= 1 else f"{size_bytes} bytes"

    subject = f"Receiptory: Backup completed \u2014 {size_str}"

    caption = (
        "\u2705 <b>Backup Completed</b>\n"
        f"Type: {backup_type} | Size: {size_str}\n"
        f"Destination: {destination}"
    )

    html = (
        "<h3>&#9989; Backup Completed</h3>"
        "<p>"
        f"<b>Type:</b> {backup_type} | <b>Size:</b> {size_str}<br>"
        f"<b>Destination:</b> {destination}"
        "</p>"
    )

    return {"subject": subject, "caption": caption, "html": html}


def format_backup_failed(error_msg: dict) -> dict:
    error = error_msg.get("error", "Unknown error") if isinstance(error_msg, dict) else str(error_msg)

    subject = "Receiptory: Backup failed"

    caption = (
        "\u274c <b>Backup Failed</b>\n"
        f"Error: {error}"
    )

    html = (
        "<h3>&#10060; Backup Failed</h3>"
        f"<p><b>Error:</b> {error}</p>"
    )

    return {"subject": subject, "caption": caption, "html": html}


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
