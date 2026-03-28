import json
import re
import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)


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


@dataclass
class LLMExtractionResult:
    extraction: ExtractionResult
    tokens_in: int
    tokens_out: int
    model: str


def build_extraction_prompt(business_names: list[str], business_addresses: list[str], business_tax_ids: list[str], categories: list[dict[str, str]]) -> str:
    category_list = "\n".join(f"  - {c['name']}: {c.get('description', '')}" for c in categories)
    return f"""You are a document data extraction system. Analyze the provided document image(s) and extract all structured data.

## User's Business Information (for identifying issued invoices vs expense receipts)
- Business names: {json.dumps(business_names)}
- Business addresses: {json.dumps(business_addresses)}
- Business tax IDs: {json.dumps(business_tax_ids)}

If the document's issuer (vendor) matches any of the above business names, addresses, or tax IDs, classify it as "issued_invoice". Otherwise, classify as "expense_receipt" for financial documents or "other_document" for non-financial documents.

## Available Categories
{category_list}

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
- category: one of the category names listed above
- extraction_confidence: 0.0 to 1.0 confidence score

Return ONLY the JSON object, no markdown fences or explanation."""


def parse_llm_response(response_text: str) -> ExtractionResult:
    text = response_text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"LLM returned invalid JSON. Raw response:\n{text[:2000]}")
        raise ValueError(f"Failed to parse LLM response as JSON: {e}")
    return ExtractionResult(
        receipt_date=data.get("receipt_date"), document_title=data.get("document_title"),
        vendor_name=data.get("vendor_name"), vendor_tax_id=data.get("vendor_tax_id"),
        vendor_receipt_id=data.get("vendor_receipt_id"), client_name=data.get("client_name"),
        client_tax_id=data.get("client_tax_id"), description=data.get("description"),
        line_items=data.get("line_items", []), subtotal=data.get("subtotal"),
        tax_amount=data.get("tax_amount"), total_amount=data.get("total_amount"),
        currency=data.get("currency"), payment_method=data.get("payment_method"),
        payment_identifier=data.get("payment_identifier"), language=data.get("language"),
        additional_fields=data.get("additional_fields", []),
        raw_extracted_text=data.get("raw_extracted_text"),
        document_type=data.get("document_type"), category_name=data.get("category"),
        extraction_confidence=data.get("extraction_confidence"),
    )


def litellm_completion(**kwargs):
    return litellm.completion(**kwargs)


def extract_document(page_images: list[bytes], model: str, api_key: str, business_names: list[str], business_addresses: list[str], business_tax_ids: list[str], categories: list[dict[str, str]], temperature: float = 0.0, max_tokens: int = 8192) -> LLMExtractionResult:
    prompt = build_extraction_prompt(business_names=business_names, business_addresses=business_addresses, business_tax_ids=business_tax_ids, categories=categories)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img_bytes in page_images:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    response = litellm_completion(model=model, api_key=api_key, messages=[{"role": "user", "content": content}], temperature=temperature, max_tokens=max_tokens)
    raw_content = response.choices[0].message.content
    logger.debug(f"LLM response ({response.usage.completion_tokens} tokens):\n{raw_content[:500]}")
    extraction = parse_llm_response(raw_content)
    return LLMExtractionResult(extraction=extraction, tokens_in=response.usage.prompt_tokens, tokens_out=response.usage.completion_tokens, model=model)
