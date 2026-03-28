import json
import os
import logging
from datetime import datetime, timezone

from backend.database import get_connection
from backend.config import get_setting
from backend.storage import (get_file_path, save_filed, render_all_pages_to_memory, get_pdf_page_count)
from backend.processing.normalize import normalize_file
from backend.processing.extract import extract_document, ExtractionResult
from backend.processing.filing import generate_stored_filename

logger = logging.getLogger(__name__)


def process_document(doc_id: int, data_dir: str) -> None:
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc is None:
        logger.error(f"Document {doc_id} not found")
        return
    try:
        _run_pipeline(doc_id, doc, data_dir)
    except Exception as e:
        logger.error(f"Processing failed for document {doc_id}: {e}")
        with get_connection() as conn:
            conn.execute("""UPDATE documents SET status = 'failed', processing_error = ?, processing_attempts = processing_attempts + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?""", (str(e), doc_id))


def _run_pipeline(doc_id: int, doc: dict, data_dir: str) -> None:
    file_hash = doc["file_hash"]
    original_ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"
    original_path = get_file_path("original", file_hash, original_ext, data_dir)
    norm = normalize_file(original_path, data_dir)
    pdf_path = norm.pdf_path
    page_count = norm.page_count
    if norm.converted:
        from backend.storage import save_converted
        save_converted(pdf_path, file_hash, data_dir)
    dpi = get_setting("page_render_dpi")
    page_images = render_all_pages_to_memory(pdf_path, dpi=dpi)
    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")
    business_names = get_setting("business_names")
    business_addresses = get_setting("business_addresses")
    business_tax_ids = get_setting("business_tax_ids")
    confidence_threshold = get_setting("confidence_threshold")
    with get_connection() as conn:
        cats = conn.execute("SELECT name, description FROM categories WHERE is_deleted = 0 AND is_system = 0").fetchall()
    categories = [{"name": c["name"], "description": c["description"] or ""} for c in cats]
    llm_result = extract_document(page_images=page_images, model=model, api_key=api_key, business_names=business_names, business_addresses=business_addresses, business_tax_ids=business_tax_ids, categories=categories)
    ext = llm_result.extraction
    doc_type = ext.document_type
    if ext.vendor_tax_id and ext.vendor_tax_id in business_tax_ids:
        doc_type = "issued_invoice"
    status = "processed"
    if ext.extraction_confidence is not None and ext.extraction_confidence < confidence_threshold:
        status = "needs_review"
    stored_filename = generate_stored_filename(receipt_date=ext.receipt_date, vendor_receipt_id=ext.vendor_receipt_id, file_hash=file_hash)
    save_filed(pdf_path, stored_filename, data_dir)
    category_id = None
    if ext.category_name:
        with get_connection() as conn:
            cat_row = conn.execute("SELECT id FROM categories WHERE name = ? AND is_deleted = 0", (ext.category_name,)).fetchone()
            if cat_row:
                category_id = cat_row["id"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection() as conn:
        conn.execute("""UPDATE documents SET document_type = ?, stored_filename = ?, page_count = ?, receipt_date = ?, document_title = ?, vendor_name = ?, vendor_tax_id = ?, vendor_receipt_id = ?, client_name = ?, client_tax_id = ?, description = ?, line_items = ?, subtotal = ?, tax_amount = ?, total_amount = ?, currency = ?, payment_method = ?, payment_identifier = ?, language = ?, additional_fields = ?, raw_extracted_text = ?, category_id = ?, status = ?, extraction_confidence = ?, processing_model = ?, processing_tokens_in = ?, processing_tokens_out = ?, processing_cost_usd = ?, processing_date = ?, processing_attempts = processing_attempts + 1, processing_error = NULL, updated_at = ? WHERE id = ?""",
            (doc_type, stored_filename, page_count, ext.receipt_date, ext.document_title, ext.vendor_name, ext.vendor_tax_id, ext.vendor_receipt_id, ext.client_name, ext.client_tax_id, ext.description, json.dumps(ext.line_items) if ext.line_items else None, ext.subtotal, ext.tax_amount, ext.total_amount, ext.currency, ext.payment_method, ext.payment_identifier, ext.language, json.dumps(ext.additional_fields) if ext.additional_fields else None, ext.raw_extracted_text, category_id, status, ext.extraction_confidence, llm_result.model, llm_result.tokens_in, llm_result.tokens_out, _estimate_cost(llm_result.model, llm_result.tokens_in, llm_result.tokens_out), now, now, doc_id))
    logger.info(f"Document {doc_id} processed successfully: {status}")


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = {"gemini/gemini-3-flash-preview": (0.10, 0.40), "gpt-4o": (2.50, 10.00), "claude-sonnet-4-20250514": (3.00, 15.00)}
    in_rate, out_rate = pricing.get(model, (1.0, 3.0))
    return (tokens_in * in_rate + tokens_out * out_rate) / 1_000_000
