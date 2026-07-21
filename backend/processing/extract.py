import json
import math
import re
import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)


class ParseFailure(ValueError):
    """Raised when the tolerant parse ladder cannot recover a document object
    from the LLM response. Distinct from the plain ValueErrors extract_document
    raises for truncation / empty / content-filter responses: only ParseFailure
    is retryable (issue #12) — a garbage response is often transient at
    temperature 1.0, whereas a max_tokens cutoff or a safety block will not
    change on re-call. Subclasses ValueError so existing `except ValueError`
    callers keep working."""


@dataclass
class ExtractionResult:
    receipt_date: str | None = None
    document_title: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_receipt_id: str | None = None
    client_name: str | None = None
    client_tax_id: str | None = None
    description: str | None = None
    line_items: list[dict] = field(default_factory=list)
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    currency: str | None = None
    payment_method: str | None = None
    payment_identifier: str | None = None
    language: str | None = None
    additional_fields: list[dict] = field(default_factory=list)
    raw_extracted_text: str | None = None
    document_type: str | None = None
    category_name: str | None = None
    extraction_confidence: float | None = None
    # Parse metadata, not document data: True when the fence/salvage tiers
    # (T2/T3) recovered the payload — the model deviated from instructions.
    parse_salvaged: bool = False


@dataclass
class LLMExtractionResult:
    extraction: ExtractionResult
    tokens_in: int
    tokens_out: int
    model: str


def build_extraction_prompt(business_names: list[str], business_addresses: list[str], business_tax_ids: list[str], expense_categories: list[dict[str, str]], issued_categories: list[dict[str, str]]) -> str:
    expense_list = "\n".join(f"  - {c['name']}: {c.get('description', '')}" for c in expense_categories)
    issued_list = "\n".join(f"  - {c['name']}: {c.get('description', '')}" for c in issued_categories)
    return f"""You are a document data extraction system. Analyze the provided document image(s) and extract all structured data.

## User's Business Information (for identifying issued invoices vs expense receipts)
- Business names: {json.dumps(business_names)}
- Business addresses: {json.dumps(business_addresses)}
- Business tax IDs: {json.dumps(business_tax_ids)}

If the document's issuer (vendor) matches any of the above business names, addresses, or tax IDs, classify it as "issued_invoice". Otherwise, classify as "expense_receipt" for financial documents or "other_document" for non-financial documents.

## Expense Categories (use when document is NOT issued by the user's business)
{expense_list}

## Issued Document Categories (use when document IS issued by the user's business)
{issued_list}

Pick the category from the appropriate section above based on whether the document is issued or received.

## Required Output
Return a single JSON object with these fields:
- receipt_date: date on the document (YYYY-MM-DD format, or null)
- document_title: title as it appears on the document
- vendor_name: vendor/issuer name
- vendor_tax_id: business number / tax ID of the issuer
- vendor_receipt_id: receipt/invoice number
- client_name: client/buyer name (if present)
- client_tax_id: client/buyer tax ID (if present)
- description: brief summary of the purchase/service/document
- line_items: array of {{"description": "...", "quantity": N, "unit_price": N}}
- subtotal: pre-tax amount (null for non-financial)
- tax_amount: tax amount (null for non-financial)
- total_amount: total amount (null for non-financial)
- currency: ISO 4217 code (ILS, USD, EUR, etc.)
- payment_method: cash, credit_card, bank_transfer, etc. (if detectable)
- payment_identifier: card last digits, account number, etc.
- language: detected language code (he, en, ru, etc.)
- additional_fields: array of {{"key": "...", "value": "..."}} for any other extracted data
- raw_extracted_text: full OCR text of the entire document
- document_type: "expense_receipt", "issued_invoice", or "other_document"
- category: one of the category names listed above (from the appropriate section)
- extraction_confidence: 0.0 to 1.0 confidence score

Return ONLY the JSON object, no markdown fences or explanation."""


_JSON_DECODER = json.JSONDecoder()
_FENCE_OPEN_RE = re.compile(r"```(?:json)?", re.IGNORECASE)
# Shape gate: a decoded dict must share at least TWO keys with the extraction
# schema, or it is a decoy/garbage/fragment (e.g. {} from JSON mode, an example
# object inside model prose, or a lone line item from a truncated response)
# and the candidate fails rather than filing an all-None record as processed.
_EXPECTED_KEYS = frozenset({
    "receipt_date", "document_title", "vendor_name", "vendor_tax_id", "vendor_receipt_id",
    "client_name", "client_tax_id", "description", "line_items", "subtotal", "tax_amount",
    "total_amount", "currency", "payment_method", "payment_identifier", "language",
    "additional_fields", "raw_extracted_text", "document_type", "category", "extraction_confidence",
})
# A parse recovered via the fence or salvage tier means the model deviated
# from instructions — trust the result a little less.
_SALVAGE_CONFIDENCE_FACTOR = 0.9
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keep \t and \n
_TRAILING_SNIPPET_CHARS = 500
_FAILURE_LOG_CHARS = 2000
_MAX_SALVAGE_ATTEMPTS = 1000
# Safety cap on llm_parse_retries: retries are sequential paid LLM calls on the
# single-process queue, so a misconfigured huge value would block the queue and
# run up cost on one poison document. 5 is far above the transient-failure need
# (issue #12 saw success on the first retry).
_MAX_PARSE_RETRIES = 5


def _sanitize_for_log(text: str) -> str:
    """Strip control chars (except tab/newline) so LLM/OCR content can't forge or corrupt log records (ANSI escapes etc.)."""
    return _CONTROL_CHARS_RE.sub(" ", text)


def _as_float(value: Any) -> float | None:
    """json_object mode guarantees valid JSON, not types — models sometimes emit numbers as strings.

    Rejects bools (True would coerce to 1.0) and non-finite values: Python's
    json parser accepts NaN/Infinity constants, and NaN defeats comparison
    gates (NaN < x is always False) while sqlite3 silently stores it as NULL.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _decode_leading_object(text: str, start: int = 0) -> tuple[dict, str] | None:
    """raw_decode a JSON value at start (skipping whitespace); accept only extraction-shaped objects.

    Returns (payload, trailing_text), or None when the text at start does
    not begin with valid JSON, the leading value is not a dict, or the dict
    shares no keys with the extraction schema (a decoy/garbage object). All
    of these are tier failures, not errors — the caller's ladder falls
    through to the next tier. RecursionError guards against pathologically
    nested input from the semi-trusted LLM, which the json module raises
    instead of JSONDecodeError.
    """
    while start < len(text) and text[start].isspace():
        start += 1
    try:
        data, end = _JSON_DECODER.raw_decode(text, start)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    # >=2 matching keys: a single shared key is not evidence of the payload —
    # a truncated response's inner line item ({"description", "quantity",
    # "unit_price"}) intersects the schema on exactly one key and must not
    # be salvaged as the whole document.
    if len(_EXPECTED_KEYS.intersection(data)) < 2:
        return None
    return data, text[end:]


def parse_llm_response(response_text: str) -> ExtractionResult:
    """Parse the LLM's extraction response, tolerating junk around the JSON.

    Gemini intermittently keeps generating after emitting a complete JSON
    object (issue #10), so parsing walks a tolerance ladder instead of a
    strict json.loads():

        response_text
            | strip()
            v
        T1: raw_decode(text) ------------------- shaped dict? --> use it
            | fail                                                ^
            v                                                     |
        T2: best-match salvage — candidates are the object        |
            after a fence opening (```json / ```, closing         |
            fence = trailing data) PLUS raw_decode at each        |
            successive "{" (capped at _MAX_SALVAGE_ATTEMPTS).     |
            The shaped dict sharing the MOST schema keys wins     |
            (ties -> fence candidate) + salvage WARNING ----------+
            | all fail
            v
        T3: ERROR log with head AND tail of response --> ValueError

    "Shaped dict" = a JSON object sharing >=2 keys with the extraction
    schema (_EXPECTED_KEYS) — a single shared key is a fragment/decoy, not
    the payload. Non-dicts (number/string/array) and unshaped objects are
    candidate failures — ValueError is raised only when the whole ladder
    is exhausted. Any salvaged result (fence or scan) carries a confidence
    penalty (_SALVAGE_CONFIDENCE_FACTOR) since the model deviated from
    instructions. Non-whitespace trailing data after the decoded object
    logs a WARNING with a snippet of what the model appended.
    Head/tail/snippet logs contain document text — accepted for a
    self-hosted single-user deployment.
    """
    text = response_text.strip()

    salvaged = False
    decoded = _decode_leading_object(text)
    if decoded is None:
        # Best-match salvage: the fence candidate (if any) and every "{"
        # candidate compete on schema-key overlap — the real payload
        # (~21 keys) always beats a prose-embedded or fenced example object,
        # so a decoy earlier in the response can't hijack the parse. The
        # fence candidate wins ties (it's the more explicit structure).
        best: tuple[dict, str] | None = None
        best_matched = 0
        fence = _FENCE_OPEN_RE.search(text)
        fence_candidate = _decode_leading_object(text, fence.end()) if fence else None
        if fence_candidate is not None:
            best, best_matched = fence_candidate, len(_EXPECTED_KEYS.intersection(fence_candidate[0]))
        idx = text.find("{")
        attempts = 0
        while idx != -1 and attempts < _MAX_SALVAGE_ATTEMPTS:
            candidate = _decode_leading_object(text, idx)
            attempts += 1
            if candidate is not None:
                matched = len(_EXPECTED_KEYS.intersection(candidate[0]))
                if matched > best_matched:
                    best, best_matched = candidate, matched
            idx = text.find("{", idx + 1)
        if best is not None:
            decoded = best
            salvaged = True
            if best is fence_candidate:
                logger.warning("LLM response wrapped the JSON object in a markdown fence despite prompt/json_mode instructions")
            else:
                logger.warning("LLM response required salvage parsing: JSON object found mid-response after non-JSON leading text")
    if decoded is None:
        if len(text) <= 2 * _FAILURE_LOG_CHARS:
            logged = text
        else:
            logged = f"{text[:_FAILURE_LOG_CHARS]}\n... [{len(text) - 2 * _FAILURE_LOG_CHARS} chars omitted] ...\n{text[-_FAILURE_LOG_CHARS:]}"
        logger.error(f"LLM returned invalid JSON. Raw response (head and tail):\n{_sanitize_for_log(logged)}")
        # Re-decode once to carry the parse detail into the stored
        # processing_error and failure notification (#215 was diagnosed
        # from exactly this detail).
        try:
            value, _ = _JSON_DECODER.raw_decode(text)
        except (json.JSONDecodeError, RecursionError) as e:
            detail = str(e)
        else:
            detail = "leading JSON object does not match the extraction schema" if isinstance(value, dict) else "leading JSON value is not an object"
        raise ParseFailure(f"Failed to parse LLM response as JSON: {detail}")

    data, trailing = decoded
    trailing = trailing.strip()
    if trailing.startswith("```"):
        trailing = trailing.removeprefix("```").strip()
    if trailing:
        logger.warning(f"LLM response contained trailing data after the JSON object; ignored. Trailing snippet: {_sanitize_for_log(trailing[:_TRAILING_SNIPPET_CHARS])}")
    line_items = data.get("line_items", [])
    if isinstance(line_items, list):
        # Same NaN/string-number defense as the scalar money fields: a NaN
        # unit_price would serialize as invalid JSON (json.dumps allow_nan)
        # and break every client-side JSON.parse of the stored line items.
        for item in line_items:
            if isinstance(item, dict):
                for key in ("quantity", "unit_price"):
                    if key in item:
                        item[key] = _as_float(item[key])
    else:
        line_items = []
    confidence = _as_float(data.get("extraction_confidence"))
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        logger.warning(f"LLM returned out-of-range extraction_confidence {confidence}; treating as unknown")
        confidence = None
    if salvaged and confidence is not None:
        confidence = round(confidence * _SALVAGE_CONFIDENCE_FACTOR, 4)
        logger.warning(f"Salvaged parse: extraction confidence penalized by {round((1 - _SALVAGE_CONFIDENCE_FACTOR) * 100)}% to {confidence}")
    return ExtractionResult(
        receipt_date=data.get("receipt_date"), document_title=data.get("document_title"),
        vendor_name=data.get("vendor_name"), vendor_tax_id=data.get("vendor_tax_id"),
        vendor_receipt_id=data.get("vendor_receipt_id"), client_name=data.get("client_name"),
        client_tax_id=data.get("client_tax_id"), description=data.get("description"),
        line_items=line_items, subtotal=_as_float(data.get("subtotal")),
        tax_amount=_as_float(data.get("tax_amount")), total_amount=_as_float(data.get("total_amount")),
        currency=data.get("currency"), payment_method=data.get("payment_method"),
        payment_identifier=data.get("payment_identifier"), language=data.get("language"),
        additional_fields=data.get("additional_fields", []),
        raw_extracted_text=data.get("raw_extracted_text"),
        document_type=data.get("document_type"), category_name=data.get("category"),
        extraction_confidence=confidence,
        parse_salvaged=salvaged,
    )


def litellm_completion(**kwargs):
    return litellm.completion(**kwargs)


def extract_document(page_images: list[bytes], model: str, api_key: str, business_names: list[str], business_addresses: list[str], business_tax_ids: list[str], expense_categories: list[dict[str, str]], issued_categories: list[dict[str, str]], temperature: float = 1.0, max_tokens: int = 8192, json_mode: bool = True, parse_retries: int = 0) -> LLMExtractionResult:
    prompt = build_extraction_prompt(business_names=business_names, business_addresses=business_addresses, business_tax_ids=business_tax_ids, expense_categories=expense_categories, issued_categories=issued_categories)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img_bytes in page_images:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    completion_kwargs: dict[str, Any] = dict(model=model, api_key=api_key, messages=[{"role": "user", "content": content}], temperature=temperature, max_tokens=max_tokens)
    if json_mode:
        # JSON mode stops trailing junk at the source (issue #10). drop_params
        # is a blanket litellm flag: on a model without response_format support
        # it silently drops ANY unsupported param (potentially temperature or
        # max_tokens too) instead of erroring — accepted so a model swap can
        # never brick extraction; the tolerant parser is the net.
        completion_kwargs["response_format"] = {"type": "json_object"}
        completion_kwargs["drop_params"] = True
    truncation_error = f"LLM response truncated at max_tokens={max_tokens} — increase the llm_max_tokens setting"
    # Retry the LLM call on PARSE failures only (issue #12). A single
    # unparseable response is often transient at temperature 1.0 (doc #215
    # re-ran clean 5/5). The retry loop must wrap the WHOLE block below so the
    # ordering is unambiguous: truncation / empty / content-filter responses
    # raise plain ValueError and propagate immediately (re-calling won't change
    # a max_tokens cutoff or a safety block, only burn tokens), while only a
    # non-truncated ParseFailure loops. litellm's own num_retries can't cover
    # this — the HTTP call succeeded (200); it's the *content* our ladder can't
    # parse. Tokens accumulate BEFORE each parse, so a doc that returns after a
    # retry bills every attempt (including the failed parses) — processing_cost
    # reflects the real spend on the SUCCESS path. On a hard-fail path
    # (truncation/empty/content-type raises after one or more attempts) the
    # accumulated totals are discarded, exactly as any failed doc records no
    # cost today.
    # Coerce and clamp the retry count. get_setting only runs the ENV override
    # through _parse_value; a DB settings row returns raw json (could be a
    # string "2", a float, or None), and range() is type-strict — so guard
    # here rather than trust the caller. Negative -> 0 (an empty range would
    # fall off the end returning None and crash the pipeline); over-large ->
    # capped (see _MAX_PARSE_RETRIES).
    try:
        retries = int(parse_retries)
    except (TypeError, ValueError):
        logger.warning(f"llm_parse_retries={parse_retries!r} is not an integer; treating as 0")
        retries = 0
    if retries < 0:
        logger.warning(f"llm_parse_retries={retries} is negative; treating as 0")
        retries = 0
    if retries > _MAX_PARSE_RETRIES:
        logger.warning(f"llm_parse_retries={retries} exceeds the cap; clamping to {_MAX_PARSE_RETRIES}")
        retries = _MAX_PARSE_RETRIES
    tokens_in_total = 0
    tokens_out_total = 0
    for attempt in range(retries + 1):
        response = litellm_completion(**completion_kwargs)
        usage = response.usage
        tokens_in_total += getattr(usage, "prompt_tokens", 0) or 0
        tokens_out_total += getattr(usage, "completion_tokens", 0) or 0
        choice = response.choices[0]
        raw_content = choice.message.content
        finish_reason = getattr(choice, "finish_reason", None)
        truncated = finish_reason == "length"
        if not raw_content:
            if truncated:
                raise ValueError(truncation_error)
            # Gemini safety/recitation blocks arrive as finish_reason=content_filter
            # with empty content — carry the reason so the stored error is diagnosable.
            raise ValueError(f"LLM returned an empty response (finish_reason={finish_reason})")
        if not isinstance(raw_content, str):
            raise ValueError(f"LLM returned unexpected content type: {type(raw_content).__name__}")
        logger.debug(f"LLM response ({getattr(usage, 'completion_tokens', 0)} tokens):\n{_sanitize_for_log(raw_content[:500])}")
        try:
            extraction = parse_llm_response(raw_content)
        except ParseFailure as e:
            # A response that rambled into the token cap may still carry a
            # complete leading object (the issue #10 shape) — only when the
            # ladder can't recover one is truncation the actionable error.
            # Truncation is deterministic, not transient: do NOT retry it.
            if truncated:
                logger.error(f"LLM response truncated at max_tokens={max_tokens} and no complete JSON object found. Tail:\n{_sanitize_for_log(raw_content[-_FAILURE_LOG_CHARS:])}")
                raise ValueError(truncation_error)
            # Non-truncated parse failure: retry if attempts remain, else fail
            # the document with the last parse error (unchanged from today).
            if attempt < retries:
                logger.warning(f"LLM response parse failed (attempt {attempt + 1}/{retries + 1}), retrying: {e}")
                continue
            raise
        if truncated:
            # A T2/T3-salvaged parse of a TRUNCATED response is untrustworthy: the
            # ladder may have latched onto an inner fragment (e.g. a line item) of
            # the incomplete document. Only a clean leading object (T1) counts.
            if extraction.parse_salvaged:
                logger.error(f"LLM response truncated at max_tokens={max_tokens}; salvage tier matched only a fragment — rejecting. Tail:\n{_sanitize_for_log(raw_content[-_FAILURE_LOG_CHARS:])}")
                raise ValueError(truncation_error)
            logger.warning(f"LLM response hit max_tokens={max_tokens} but a complete leading JSON object was parsed")
        return LLMExtractionResult(extraction=extraction, tokens_in=tokens_in_total, tokens_out=tokens_out_total, model=model)
    # Unreachable: retries is clamped >= 0 so the loop runs at least once, and
    # each iteration returns or raises. Guard against a future edit that breaks
    # that invariant rather than silently returning None.
    raise RuntimeError("extract_document retry loop exited without returning")
