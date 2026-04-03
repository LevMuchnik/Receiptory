"""LLM-based triage to decide which URLs/attachments to ingest."""

import json
import logging
import re
from dataclasses import dataclass, field

from backend.config import get_setting
from backend.processing.extract import litellm_completion

logger = logging.getLogger(__name__)


@dataclass
class TriageResult:
    ingest_attachments: list[str] = field(default_factory=list)
    ingest_urls: list[str] = field(default_factory=list)


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


async def triage_email(
    body_text: str, attachments: list[dict], urls: list[str]
) -> TriageResult:
    """Use LLM to decide which email attachments and URLs to ingest.

    attachments: list of dicts with keys: filename, content_type, size
    On LLM failure or missing config, falls back to ALL attachments + ALL URLs.
    """
    all_filenames = [a["filename"] for a in attachments]
    fallback = TriageResult(
        ingest_attachments=list(all_filenames),
        ingest_urls=list(urls),
    )

    if not attachments and not urls:
        return TriageResult()

    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")

    if not model or not api_key:
        logger.warning("LLM not configured for email triage, returning all content")
        return fallback

    truncated_body = body_text[:3000] if body_text else ""

    att_desc = "\n".join(
        f"- {a['filename']} (type: {a.get('content_type', 'unknown')}, size: {a.get('size', 'unknown')} bytes)"
        for a in attachments
    ) or "(none)"

    url_list = "\n".join(f"- {u}" for u in urls) or "(none)"

    prompt = (
        "You are a document triage assistant. Given an email body, its attachments, and URLs found in the email, "
        "decide which attachments and URLs are likely receipts, invoices, or financial documents worth ingesting.\n\n"
        "Decision factors:\n"
        "- PDF attachments are likely invoices or receipts\n"
        "- Small image attachments (under 10KB) are likely logos or signatures, not documents\n"
        "- Larger image attachments may be scanned receipts\n"
        "- URLs pointing to download/view invoice or receipt pages should be ingested\n"
        "- Account management, unsubscribe, marketing, and social media URLs should NOT be ingested\n"
        "- Attachments with names like 'logo', 'signature', 'banner' are not documents\n\n"
        f"Email body (truncated):\n{truncated_body}\n\n"
        f"Attachments:\n{att_desc}\n\n"
        f"URLs:\n{url_list}\n\n"
        'Return ONLY a JSON object with two keys: "ingest_attachments" (list of filenames) '
        'and "ingest_urls" (list of URLs). Only include items from the provided lists. '
        "No explanation, just JSON."
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
        if not isinstance(parsed, dict):
            logger.error("LLM returned non-dict for email triage: %s", type(parsed))
            return fallback

        filenames = parsed.get("ingest_attachments", [])
        ingest_urls = parsed.get("ingest_urls", [])

        # Filter to only items from the input lists
        valid_filenames = [f for f in filenames if f in all_filenames]
        valid_urls = [u for u in ingest_urls if u in urls]

        return TriageResult(
            ingest_attachments=valid_filenames,
            ingest_urls=valid_urls,
        )
    except Exception:
        logger.exception("LLM triage failed for email, returning all content")
        return fallback
