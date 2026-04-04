"""LLM-based triage to decide which URLs/attachments to ingest."""

import base64
import json
import logging
import re
from dataclasses import dataclass

from backend.config import get_setting
from backend.processing.extract import litellm_completion

logger = logging.getLogger(__name__)


@dataclass
class ClassificationDocument:
    identifier: str  # filename (for attachments) or URL (for fetched docs)
    source: str  # "attachment" or "url"
    first_page_image: bytes  # PNG bytes of first page


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences from LLM response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


async def triage_telegram_urls(message_text: str, urls: list[str]) -> list[str]:
    """Use LLM to filter URLs that are likely receipts/invoices/financial documents.

    On LLM failure or missing config, falls back to returning ALL urls.
    """
    if not urls:
        return []

    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")

    if not model or not api_key:
        logger.warning("LLM not configured for URL triage, returning all URLs")
        return list(urls)

    url_list = "\n".join(f"- {u}" for u in urls)
    prompt = (
        "You are a document triage assistant. Given a Telegram message and a list of URLs, "
        "determine which URLs are likely links to receipts, invoices, financial documents, "
        "or downloadable purchase confirmations.\n\n"
        "Exclude URLs that are:\n"
        "- Tracking/shipping links\n"
        "- Marketing or promotional links\n"
        "- Social media links\n"
        "- General news or blog articles\n"
        "- App store links\n\n"
        f"Message text:\n{message_text}\n\n"
        f"URLs found in message:\n{url_list}\n\n"
        "Return ONLY a JSON array of URLs to ingest (must be from the provided list). "
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
            logger.error("LLM returned non-list for telegram triage: %s", type(parsed))
            return list(urls)
        # Only return URLs that were in the input list
        return [u for u in parsed if u in urls]
    except Exception:
        logger.exception("LLM triage failed for telegram URLs, returning all")
        return list(urls)


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

    try:
        model = get_setting("llm_model")
        api_key = get_setting("llm_api_key")
    except RuntimeError:
        logger.warning("Database not available for URL triage settings, returning all URLs")
        return fallback

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

    try:
        model = get_setting("llm_model")
        api_key = get_setting("llm_api_key")
    except RuntimeError:
        logger.warning("Database not available for document classification settings, returning all")
        return fallback

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
