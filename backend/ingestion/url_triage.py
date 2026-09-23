"""LLM-based triage to decide which URLs/attachments to ingest."""

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass

from backend.config import get_setting, resolve_llm_api_key
from backend.processing.extract import complete_with_schema_fallback, reasoning_effort_kwargs, response_format_kwargs, strict_object_schema

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


_JSON_DECODER = json.JSONDecoder()
# Each triage call is a blocking HTTP request run in a worker thread. Without a
# timeout litellm waits up to 600s on a provider that hangs.
_TRIAGE_TIMEOUT_S = 60


def _parse_selection(raw: str, key: str) -> list | None:
    """The model's pick: `{"<key>": [...]}` as the schema asks, or a bare list
    from a model that ignored it. None when the reply is JSON but neither;
    raises when the reply does not start with JSON at all (the caller keeps
    everything either way).

    Only the LEADING JSON value is read. Without a schema, Gemini can append
    prose after complete JSON (issue #10), and a strict json.loads would call
    that "Extra data" and fall back to keeping every link.
    """
    text = _strip_code_fences(raw)
    parsed, end = _JSON_DECODER.raw_decode(text)
    trailing = text[end:].strip()
    if trailing:
        logger.warning("LLM triage reply had trailing data after the JSON; ignored: %.200s", trailing)
    if isinstance(parsed, dict):
        parsed = parsed.get(key)
    return parsed if isinstance(parsed, list) else None


def _llm_select(content: str | list[dict], key: str, allowed: list[str], label: str) -> list[str]:
    """Ask the model which of `allowed` to keep. Any failure keeps them all.

    The one path all three triage calls share: settings, the call, the schema,
    parsing, and filtering to what was offered. Keeping everything on failure is
    deliberate (issue #67 review, decision 2A): a junk document is visible and
    costs one delete, while a dropped receipt is invisible and never arrives.

    The reply is wrapped in an object (`{"<key>": [...]}`) because OpenAI's
    json_object mode and strict schemas both require a top-level object.
    """
    fallback = list(allowed)
    try:
        model = get_setting("llm_model")
        api_key = resolve_llm_api_key()
        temperature = get_setting("llm_temperature")
        reasoning_effort = get_setting("llm_reasoning_effort")
        json_mode = get_setting("llm_json_mode")
    except RuntimeError:
        logger.warning("Database not available for %s settings, keeping all", label)
        return fallback

    if not model or not api_key:
        logger.warning("LLM not configured for %s, keeping all", label)
        return fallback

    completion_kwargs: dict = dict(model=model, api_key=api_key, messages=[{"role": "user", "content": content}], temperature=temperature, timeout=_TRIAGE_TIMEOUT_S)
    if json_mode:
        schema = strict_object_schema({key: {"type": "array", "items": {"type": "string"}}})
        completion_kwargs.update(response_format_kwargs(model, f"{key}_selection", schema))
    completion_kwargs.update(reasoning_effort_kwargs(model, reasoning_effort))

    try:
        # Same one-shot json_object retry as extraction if the provider rejects
        # the schema: otherwise a model that refuses it would turn triage into
        # keep-everything on every message, with nothing but a log line to show.
        response = complete_with_schema_fallback(completion_kwargs)
        selected = _parse_selection(response.choices[0].message.content, key)
    except Exception:
        logger.exception("LLM %s failed, keeping all", label)
        return fallback
    if selected is None:
        logger.error("LLM returned no %r list for %s, keeping all", key, label)
        return fallback
    # Only what was offered (the model can echo a URL it invented), each once:
    # a URL listed twice would be fetched twice.
    return list(dict.fromkeys(item for item in selected if isinstance(item, str) and item in allowed))


async def _select_off_loop(content: str | list[dict], key: str, allowed: list[str], label: str) -> list[str]:
    """_llm_select in a worker thread. The LLM call is blocking, and the Telegram
    bot runs on FastAPI's own event loop: made inline, one message with a link
    would stall every API request, the processing queue and the backup
    scheduler for as long as the provider took to answer."""
    return await asyncio.to_thread(_llm_select, content, key, allowed, label)


async def triage_telegram_urls(message_text: str, urls: list[str]) -> list[str]:
    """Use LLM to filter URLs that are likely receipts/invoices/financial documents.

    On LLM failure or missing config, falls back to returning ALL urls.
    """
    if not urls:
        return []

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
        'Return ONLY a JSON object of the form {"urls": [...]} listing the URLs to ingest '
        '(each must be from the provided list). If none are relevant, return {"urls": []}. '
        "No explanation, just JSON."
    )
    return await _select_off_loop(prompt, "urls", urls, "telegram URL triage")


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
        'Return ONLY a JSON object of the form {"urls": [...]} listing the URLs to fetch '
        '(each must be from the provided list). If none are relevant, return {"urls": []}. '
        "No explanation, just JSON."
    )
    return await _select_off_loop(prompt, "urls", urls, "email URL triage")


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
        'Return ONLY a JSON object of the form {"identifiers": [...]} listing the identifiers '
        '(from the list above) that are real financial documents. If none qualify, return '
        '{"identifiers": []}. No explanation, just JSON.'
    )

    content: list[dict] = [{"type": "text", "text": prompt}]
    for doc in documents:
        b64 = base64.b64encode(doc.first_page_image).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    return await _select_off_loop(content, "identifiers", [d.identifier for d in documents], "email document classification")
