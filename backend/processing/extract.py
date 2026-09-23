import copy
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
    # The response_format the successful call used: "json_schema", "json_object",
    # "json_object_fallback" (a schema was requested and rejected), or "none".
    # scripts/compare_json_mode.py records it, so an A/B run that silently fell
    # back cannot pass itself off as a measurement of the schema.
    output_mode: str = "none"


_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_NUMBER = {"type": ["number", "null"]}
DOCUMENT_TYPES = ("expense_receipt", "issued_invoice", "other_document")


def strict_object_schema(properties: dict[str, dict]) -> dict:
    """Every property required, nothing else allowed: the shape OpenAI strict
    mode demands, so one schema serves every provider litellm can route to.
    Optional values are expressed as nullable types, never as missing keys."""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


# The fields the model returns, in the order it writes them. One list, three
# consumers: the response schema (build_extraction_schema), the parser's shape
# gate (_EXPECTED_KEYS), and the prompt's "Required Output" list, which
# tests/test_extract.py pins to this exact order.
#
# The order is load-bearing: under a schema the model emits keys in declaration
# order, and what it has written is its working memory for what follows. Moving
# raw_extracted_text FIRST (transcribe, then extract) was tried in issue #67 and
# measured against master on 26 real documents: no gain on misdated years
# (7 -> 6 of 22 correct), and alongside the schema it misread "2026" as "2020" on
# correctly dated receipts and dropped tax_amount more often. It was taken out.
# Re-run scripts/compare_json_mode.py before reordering. `category` is a plain
# nullable string here; build_extraction_schema narrows it to the categories.
_FIELD_SCHEMAS: dict[str, dict] = {
    "receipt_date": _NULLABLE_STRING,
    "document_title": _NULLABLE_STRING,
    "vendor_name": _NULLABLE_STRING,
    "vendor_tax_id": _NULLABLE_STRING,
    "vendor_receipt_id": _NULLABLE_STRING,
    "client_name": _NULLABLE_STRING,
    "client_tax_id": _NULLABLE_STRING,
    "description": _NULLABLE_STRING,
    # description is a plain string, NOT nullable: models.LineItem.description
    # is `str`, and GET /documents validates every stored line item on the way
    # out, so one null description would 500 the whole document list. Under
    # json_object mode the model never returned one (0 of 255 stored
    # documents); a nullable schema would invite it. parse_llm_response also
    # coerces it, for the modes a schema does not bind.
    "line_items": {"type": "array", "items": strict_object_schema({
        "description": {"type": "string"}, "quantity": _NULLABLE_NUMBER, "unit_price": _NULLABLE_NUMBER,
    })},
    "subtotal": _NULLABLE_NUMBER,
    "tax_amount": _NULLABLE_NUMBER,
    "total_amount": _NULLABLE_NUMBER,
    "currency": _NULLABLE_STRING,
    "payment_method": _NULLABLE_STRING,
    "payment_identifier": _NULLABLE_STRING,
    "language": _NULLABLE_STRING,
    "additional_fields": {"type": "array", "items": strict_object_schema({
        "key": {"type": "string"}, "value": {"type": "string"},
    })},
    "raw_extracted_text": _NULLABLE_STRING,
    "document_type": {"type": "string", "enum": list(DOCUMENT_TYPES)},
    "category": _NULLABLE_STRING,
    # Required and never null: the model says "unreadable" with 0.0, which still
    # routes to needs_review. Measured before this change: a missing confidence
    # never once occurred on a successful extraction.
    "extraction_confidence": {"type": "number"},
}


def build_extraction_schema(expense_categories: list[dict[str, str]], issued_categories: list[dict[str, str]]) -> dict:
    """The JSON Schema the extraction call asks the model to fill.

    `category` becomes an enum of the user's category names plus null. Names are
    deduplicated across the two sections: the enum cannot tell them apart, and
    pipeline.py still checks the returned name against the expected section, so
    a wrong-section pick is NULLed there exactly as it was before the schema. A
    user with no categories gets the plain nullable string, because an empty
    `enum` is not valid JSON Schema.

    Deep-copied: the fragments in _FIELD_SCHEMAS are shared module state.
    """
    properties = copy.deepcopy(_FIELD_SCHEMAS)
    names = sorted({c["name"] for c in (*expense_categories, *issued_categories) if c.get("name")})
    if names:
        properties["category"] = {"type": ["string", "null"], "enum": [*names, None]}
    return strict_object_schema(properties)


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
- total_amount: the document's own stated total for what was billed, tax included
  (null for non-financial). This is the line the document calls its total -- in
  Hebrew receipts, "סה\"כ לתשלום" / "סה\"כ כולל מע\"מ". It must equal
  subtotal + tax_amount whenever both are present.
  Do NOT use the amount actually charged to a card when it differs: a tip added
  at the terminal, a rounding line, a deposit or a partial payment all change
  what was charged without changing the document's total. Put that figure in
  additional_fields instead, e.g. {{"key": "total_charged", "value": "824.00"}}
  alongside {{"key": "tip_amount", "value": "88.00"}}, and leave total_amount as
  the billed total.
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


TOTALS_TOLERANCE = 0.02
# Per-line VAT rounding grows with the invoice, so the tolerance has to as well.
# 0.1% sits in the middle of a stable range: 0.05% still flags a 435 receipt for
# a 30-agora rounding, and 0.2% changes nothing versus 0.1% on the real corpus.
ROUNDING_FRACTION = 0.001


def totals_mismatch(
    subtotal: float | None,
    tax_amount: float | None,
    total_amount: float | None,
    tolerance: float = TOTALS_TOLERANCE,
) -> float | None:
    """The unexplained gap when subtotal + tax_amount != total_amount, else None.

    A receipt states its own arithmetic, so when all three numbers are present
    they have to agree. When they do not, one of them was read off the wrong
    line -- and the one that gets read wrong is the total, because restaurant
    receipts print the card charge (bill + tip) below the billed total, and
    marketplace receipts print a shipping or discount line the same way. Filing
    that as `total_amount` silently inflates or deflates the expense: the same
    Naya receipt came in twice as 736.00 and once as 824.00 (document #314,
    2026-09-12), all three carrying subtotal 623.73 and tax 112.27.

    Returns the signed difference so the caller can say which way it is off.
    Anything past the tolerance is a real disagreement, including a legitimate
    one (shipping, a discount), which is still worth a human glance before it
    lands in an expense total.

    The tolerance is absolute OR relative, whichever is larger, because printed
    VAT is rounded per line: a 4-agora gap is noise on a 93,135 invoice and a
    wrong number on a 20 one. Measured over the owner's 244 documents, the flat
    2-agora tolerance alone flagged three invoices for their own rounding
    (-0.04 on 93,135, +0.32 on 21,448, +0.30 on 435); the 0.1% floor drops
    exactly those three and keeps all fifteen genuine disagreements, including
    the 88.00 tip that prompted this.

    Pure, so the test suite can cover it without a database or an LLM.
    """
    values = (subtotal, tax_amount, total_amount)
    if any(v is None for v in values):
        return None
    if any(not math.isfinite(v) for v in values):  # type: ignore[arg-type]
        return None
    diff = total_amount - (subtotal + tax_amount)  # type: ignore[operator]
    allowed = max(tolerance, ROUNDING_FRACTION * abs(total_amount))  # type: ignore[arg-type]
    return diff if abs(diff) > allowed else None


def format_totals_reason(
    subtotal: float, tax_amount: float, total_amount: float, diff: float
) -> str:
    """The sentence shown to the owner for a totals disagreement.

    Shared by the pipeline (which writes it at extraction time) and the document
    PATCH endpoint (which rewrites it when the numbers are edited), so the two
    cannot drift into describing the same condition differently.
    """
    return (
        f"Totals disagree: subtotal {subtotal:.2f} + tax {tax_amount:.2f} "
        f"= {subtotal + tax_amount:.2f}, but total reads {total_amount:.2f} "
        f"({diff:+.2f}). Check for a tip, shipping or discount line."
    )


_JSON_DECODER = json.JSONDecoder()
_FENCE_OPEN_RE = re.compile(r"```(?:json)?", re.IGNORECASE)
# Shape gate: a decoded dict must share at least TWO keys with the extraction
# schema, or it is a decoy/garbage/fragment (e.g. {} from JSON mode, an example
# object inside model prose, or a lone line item from a truncated response)
# and the candidate fails rather than filing an all-None record as processed.
_EXPECTED_KEYS = frozenset(_FIELD_SCHEMAS)
# A parse recovered via the fence or salvage tier means the model deviated
# from instructions — trust the result a little less.
_SALVAGE_CONFIDENCE_FACTOR = 0.9
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keep \t and \n
_TRAILING_SNIPPET_CHARS = 500
# How much of a provider's error message to log.
_ERROR_SNIPPET_CHARS = 500
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
        # Every stored item must validate as models.LineItem, because
        # GET /documents validates them all on the way out and one bad item
        # 500s the whole list. So: dicts only, and a string description (a
        # missing or null one becomes ""). The schema already forbids both;
        # json_object mode and a model that ignores the schema do not.
        kept = [item for item in line_items if isinstance(item, dict)]
        if len(kept) != len(line_items):
            logger.warning(f"Dropped {len(line_items) - len(kept)} line item(s) that were not objects")
        line_items = kept
        for item in line_items:
            description = item.get("description")
            item["description"] = "" if description is None else str(description)
            # Same NaN/string-number defense as the scalar money fields: a NaN
            # unit_price would serialize as invalid JSON (json.dumps allow_nan)
            # and break every client-side JSON.parse of the stored line items.
            for key in ("quantity", "unit_price"):
                if key in item:
                    item[key] = _as_float(item[key])
    else:
        line_items = []
    # Same guarantee for additional_fields: models.AdditionalField is
    # key: str, value: str and pydantic does not coerce 88.0 to "88.0", so one
    # numeric value would 500 GET /documents. Entries without a key are dropped.
    additional_fields = data.get("additional_fields", [])
    if isinstance(additional_fields, dict):
        # A common json_object-mode deviation: {"iban": "..."} instead of
        # [{"key": "iban", "value": "..."}]. Keep the data in the stored shape.
        additional_fields = [{"key": k, "value": v} for k, v in additional_fields.items()]
    if isinstance(additional_fields, list):
        kept = [f for f in additional_fields if isinstance(f, dict) and f.get("key") is not None]
        if len(kept) != len(additional_fields):
            logger.warning(f"Dropped {len(additional_fields) - len(kept)} additional field(s) without a key")
        additional_fields = [{"key": str(f["key"]), "value": "" if f.get("value") is None else str(f["value"])} for f in kept]
    else:
        additional_fields = []
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
        additional_fields=additional_fields,
        raw_extracted_text=data.get("raw_extracted_text"),
        document_type=data.get("document_type"), category_name=data.get("category"),
        extraction_confidence=confidence,
        parse_salvaged=salvaged,
    )


def litellm_completion(**kwargs):
    return litellm.completion(**kwargs)


def response_format_kwargs(model: str, name: str, schema: dict) -> dict[str, Any]:
    """litellm kwargs asking for `schema`, or for plain JSON where the model can't take one.

    json_schema when litellm's registry says the model supports response
    schemas; json_object otherwise, including an id the registry doesn't know
    (self-hosted, brand new). drop_params is set either way: a model with
    neither drops the param rather than erroring, so a model swap can never
    brick a call (the issue #10 invariant). It is a blanket flag and can drop
    other unsupported params too, which is accepted: the callers' parsers are
    the net. Same shape as reasoning_effort_kwargs below, and shared with
    ingestion/url_triage.py.
    """
    try:
        supported = litellm.supports_response_schema(model=model)
    except Exception:
        supported = False
    if supported:
        response_format: dict[str, Any] = {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
    else:
        response_format = {"type": "json_object"}
    return {"response_format": response_format, "drop_params": True}


EXTRACTION_SCHEMA_NAME = "receipt_extraction"


def extraction_format_kwargs(model: str, expense_categories: list[dict[str, str]], issued_categories: list[dict[str, str]]) -> dict[str, Any]:
    """The response_format kwargs extraction sends: the schema with the user's
    category enum. The settings page's LLM test sends the same kwargs with a
    minimal prompt, so it tests whether the provider accepts this schema; it
    does not re-run a whole extraction."""
    return response_format_kwargs(model, EXTRACTION_SCHEMA_NAME, build_extraction_schema(expense_categories, issued_categories))


# 400s that are not about the schema. Re-sending without it fails the same way
# (the document is still too long, the page still unsafe, the image still
# unfetchable), so these are raised as they are instead of being retried and
# reported as a rejected schema.
_NOT_SCHEMA_REJECTIONS = (litellm.ContextWindowExceededError, litellm.ContentPolicyViolationError, litellm.ImageFetchError)


def is_schema_rejection(error: BaseException) -> bool:
    """A 400 in which the provider refused the response schema.

    Gemini documents that a complex schema (an enum with many values: the
    category list is user-editable) fails with 400 InvalidArgument ("The
    specified schema produces a constraint that has too many states..."), and
    OpenAI answers "Invalid schema for response_format...". litellm raises both
    as a plain BadRequestError. So does much else: litellm 1.93 maps any Gemini
    error containing "403" (a revoked key, billing, API not enabled) to
    BadRequestError too (exception_mapping_utils.py). So the error must also
    name a schema. Its subclasses for other causes are excluded outright
    (_NOT_SCHEMA_REJECTIONS). A refusal worded without "schema" is not retried:
    the document fails with the provider's own message (issue #67 /ship, D1).
    """
    return (
        isinstance(error, litellm.BadRequestError)
        and not isinstance(error, _NOT_SCHEMA_REJECTIONS)
        and "schema" in str(error).lower()
    )


def complete_with_schema_fallback(completion_kwargs: dict[str, Any]) -> Any:
    """One completion call; if the provider rejects the schema, retry once without it.

    On a schema rejection (is_schema_rejection) of a json_schema call, switch
    to json_object and re-call rather than fail. The switch is written back
    into completion_kwargs, so the caller's later calls with the same kwargs
    (extraction's parse retries) skip the schema too. Anything else raises
    unchanged, with no extra call: a 400 on a json_object call, or a
    non-schema 400 such as ContextWindowExceededError. A rejected call returns
    no usage, so there are no tokens to account for. Shared by extraction and
    ingestion/url_triage.py.
    """
    try:
        return litellm_completion(**completion_kwargs)
    except litellm.BadRequestError as e:
        if not is_schema_rejection(e) or (completion_kwargs.get("response_format") or {}).get("type") != "json_schema":
            raise
        logger.error(f"LLM rejected the response schema ({type(e).__name__}); retrying once in json_object mode: {_sanitize_for_log(str(e)[:_ERROR_SNIPPET_CHARS])}")
        completion_kwargs["response_format"] = {"type": "json_object"}
        return litellm_completion(**completion_kwargs)


# Allowed reasoning-effort levels. "none" means "don't send the param" — the
# byte-identical-to-today path (issue #13). The rest are the OpenAI-style scale
# litellm translates per provider (Gemini thinkingBudget, Anthropic
# thinking.budget_tokens, OpenAI reasoning_effort natively).
REASONING_EFFORT_VALUES = ("none", "minimal", "low", "medium", "high")


def normalize_reasoning_effort(raw: Any) -> str:
    """Coerce an llm_reasoning_effort setting to a known value.

    get_setting only runs the ENV override through _parse_value; a DB settings
    row returns raw json (could be a string, None, or anything an old row held),
    so validate at the use site rather than trust the caller — same pattern as
    llm_parse_retries. Anything unrecognized collapses to "none" (param not
    sent), so a bad value can never brick extraction, only fail safe to today's
    behavior."""
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in REASONING_EFFORT_VALUES:
            return v
    return "none"


def reasoning_effort_kwargs(model: str, effort: Any) -> dict[str, Any]:
    """Return litellm kwargs to request reasoning at `effort`, or {} to send
    nothing.

    Returns {} when effort is "none" OR the model can't reason — gating on
    litellm.supports_reasoning keeps a non-reasoning model from raising
    UnsupportedParamsError without relying on drop_params. When we DO inject the
    param we also set drop_params=True so a reasoning model that rejects a
    specific level (a provider that lacks "minimal", say) drops it rather than
    erroring — the acceptance bar is "no UnsupportedParamsError in any
    combination". drop_params is added ONLY on the inject path, so the effort==
    "none" case stays byte-identical to today."""
    value = normalize_reasoning_effort(effort)
    if value == "none":
        return {}
    try:
        if not litellm.supports_reasoning(model=model):
            return {}
    except Exception:
        # Unknown model (not in the registry) — fail safe, don't send.
        return {}
    return {"reasoning_effort": value, "drop_params": True}


def extract_document(page_images: list[bytes], model: str, api_key: str, business_names: list[str], business_addresses: list[str], business_tax_ids: list[str], expense_categories: list[dict[str, str]], issued_categories: list[dict[str, str]], temperature: float = 1.0, max_tokens: int = 8192, json_mode: bool = True, parse_retries: int = 0, reasoning_effort: Any = "none") -> LLMExtractionResult:
    prompt = build_extraction_prompt(business_names=business_names, business_addresses=business_addresses, business_tax_ids=business_tax_ids, expense_categories=expense_categories, issued_categories=issued_categories)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img_bytes in page_images:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    completion_kwargs: dict[str, Any] = dict(model=model, api_key=api_key, messages=[{"role": "user", "content": content}], temperature=temperature, max_tokens=max_tokens)
    if json_mode:
        # Ask for the extraction schema (issue #67), falling back to plain JSON
        # mode (issue #10) on a model that can't take one. The schema binds the
        # field names, types, key order and the category list; the tolerant
        # parser stays as the net for the fallback modes.
        completion_kwargs.update(extraction_format_kwargs(model, expense_categories, issued_categories))
    # Reasoning effort (issue #13): merged in only for reasoning-capable models
    # when the setting is not "none"; on the inject path this also flips
    # drop_params=True (a no-op when json_mode already set it).
    completion_kwargs.update(reasoning_effort_kwargs(model, reasoning_effort))
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
    requested_mode = (completion_kwargs.get("response_format") or {}).get("type", "none")
    for attempt in range(retries + 1):
        response = complete_with_schema_fallback(completion_kwargs)
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
        sent_mode = (completion_kwargs.get("response_format") or {}).get("type", "none")
        output_mode = "json_object_fallback" if requested_mode == "json_schema" and sent_mode != "json_schema" else sent_mode
        return LLMExtractionResult(extraction=extraction, tokens_in=tokens_in_total, tokens_out=tokens_out_total, model=model, output_mode=output_mode)
    # Unreachable: retries is clamped >= 0 so the loop runs at least once, and
    # each iteration returns or raises. Guard against a future edit that breaks
    # that invariant rather than silently returning None.
    raise RuntimeError("extract_document retry loop exited without returning")
