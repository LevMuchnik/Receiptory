import json
import os
import logging
from datetime import datetime, timezone

from backend.database import get_connection
from backend.config import get_setting, resolve_llm_api_key
from backend.storage import (get_file_path, save_filed, remove_filed, render_all_pages_to_memory)
from backend.processing.normalize import normalize_file
from backend.processing.extract import extract_document, totals_mismatch, format_totals_reason, ExtractionResult
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
        try:
            from backend.notifications.notifier import notify
            with get_connection() as conn:
                failed_doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if failed_doc:
                notify("failed", {
                    "id": doc_id,
                    "original_filename": failed_doc["original_filename"],
                    "file_hash": failed_doc["file_hash"],
                    "processing_error": str(e),
                    "processing_attempts": (failed_doc["processing_attempts"] or 0) + 1,
                })
        except Exception:
            pass


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
    api_key = resolve_llm_api_key()
    business_names = get_setting("business_names")
    business_addresses = get_setting("business_addresses")
    business_tax_ids = get_setting("business_tax_ids")
    confidence_threshold = get_setting("confidence_threshold")
    with get_connection() as conn:
        cats = conn.execute("SELECT name, description, section FROM categories WHERE is_deleted = 0 AND is_system = 0").fetchall()
    expense_categories = [{"name": c["name"], "description": c["description"] or ""} for c in cats if c["section"] == "expense"]
    issued_categories = [{"name": c["name"], "description": c["description"] or ""} for c in cats if c["section"] == "issued"]
    temperature = get_setting("llm_temperature")
    max_tokens = get_setting("llm_max_tokens")
    json_mode = get_setting("llm_json_mode")
    parse_retries = get_setting("llm_parse_retries")
    reasoning_effort = get_setting("llm_reasoning_effort")
    llm_result = extract_document(page_images=page_images, model=model, api_key=api_key, business_names=business_names, business_addresses=business_addresses, business_tax_ids=business_tax_ids, expense_categories=expense_categories, issued_categories=issued_categories, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode, parse_retries=parse_retries, reasoning_effort=reasoning_effort)
    ext = llm_result.extraction
    doc_type = ext.document_type
    if ext.vendor_tax_id and ext.vendor_tax_id in business_tax_ids:
        doc_type = "issued_invoice"
    status = "processed"
    review_reason = None
    # Missing confidence is NOT full confidence — a response that omitted the
    # score (wrong-shaped but salvageable JSON) deserves a human eye.
    if ext.extraction_confidence is None or ext.extraction_confidence < confidence_threshold:
        status = "needs_review"
        review_reason = (
            "Extraction confidence missing"
            if ext.extraction_confidence is None
            else f"Extraction confidence {ext.extraction_confidence:.2f} is below the {confidence_threshold} threshold"
        )
    # The document's own arithmetic has to hold. When it does not, the total was
    # read off the wrong line — typically a card charge including a tip, or a
    # shipping/discount line — and filing it would put the wrong number in every
    # expense total. A confident extraction can still be wrong this way: #314
    # (2026-09-12) filed 824.00 against subtotal 623.73 + tax 112.27 at 0.98.
    diff = totals_mismatch(ext.subtotal, ext.tax_amount, ext.total_amount)
    if diff is not None:
        status = "needs_review"
        detail = format_totals_reason(ext.subtotal, ext.tax_amount, ext.total_amount, diff)
        review_reason = f"{review_reason}. {detail}" if review_reason else detail
        logger.warning(f"Document {doc_id}: {detail}")
    stored_filename = generate_stored_filename(receipt_date=ext.receipt_date, vendor_receipt_id=ext.vendor_receipt_id, file_hash=file_hash)
    previous_filename = doc["stored_filename"]
    # gstack-shortcut(dec-333b80f5): the save_filed below sits ~18 lines above
    # the UPDATE that records stored_filename, with two category queries, a
    # json.dumps over LLM data and estimate_cost's `import litellm` in between.
    # A raise in that window is caught by process_document, which sets
    # status='failed' without touching stored_filename -- so the file written
    # here is orphaned permanently, and _drop_superseded_filed_copy never runs
    # because it only fires after a committed rename. Moving this line down next
    # to the UPDATE would close it; deliberately not done. Upgrade when
    # filed_orphans climbs without a matching successful rename.
    save_filed(pdf_path, stored_filename, data_dir)
    category_id = None
    expected_section = "issued" if doc_type == "issued_invoice" else ("other" if doc_type == "other_document" else "expense")
    if ext.category_name:
        with get_connection() as conn:
            cat_row = conn.execute("SELECT id, section FROM categories WHERE name = ? AND section = ? AND is_deleted = 0", (ext.category_name, expected_section)).fetchone()
            if cat_row:
                category_id = cat_row["id"]
            else:
                # Fallback: try without section filter in case LLM returned wrong-section category
                cat_row = conn.execute("SELECT id, section FROM categories WHERE name = ? AND is_deleted = 0", (ext.category_name,)).fetchone()
                if cat_row and cat_row["section"] != expected_section:
                    logger.warning(f"Document {doc_id}: LLM returned category '{ext.category_name}' (section={cat_row['section']}) but expected section={expected_section}. Setting category to NULL.")
                    category_id = None
                elif cat_row:
                    category_id = cat_row["id"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection() as conn:
        conn.execute("""UPDATE documents SET document_type = ?, stored_filename = ?, page_count = ?, receipt_date = ?, document_title = ?, vendor_name = ?, vendor_tax_id = ?, vendor_receipt_id = ?, client_name = ?, client_tax_id = ?, description = ?, line_items = ?, subtotal = ?, tax_amount = ?, total_amount = ?, currency = ?, payment_method = ?, payment_identifier = ?, language = ?, additional_fields = ?, raw_extracted_text = ?, category_id = ?, status = ?, review_reason = ?, extraction_confidence = ?, processing_model = ?, processing_tokens_in = ?, processing_tokens_out = ?, processing_cost_usd = ?, processing_date = ?, processing_attempts = processing_attempts + 1, processing_error = NULL, updated_at = ? WHERE id = ?""",
            (doc_type, stored_filename, page_count, ext.receipt_date, ext.document_title, ext.vendor_name, ext.vendor_tax_id, ext.vendor_receipt_id, ext.client_name, ext.client_tax_id, ext.description, json.dumps(ext.line_items) if ext.line_items else None, ext.subtotal, ext.tax_amount, ext.total_amount, ext.currency, ext.payment_method, ext.payment_identifier, ext.language, json.dumps(ext.additional_fields) if ext.additional_fields else None, ext.raw_extracted_text, category_id, status, review_reason, ext.extraction_confidence, llm_result.model, llm_result.tokens_in, llm_result.tokens_out, estimate_cost(llm_result.model, llm_result.tokens_in, llm_result.tokens_out), now, now, doc_id))
    _drop_superseded_filed_copy(doc_id, previous_filename, stored_filename, data_dir)
    logger.info(f"Document {doc_id} processed successfully: {status}")
    try:
        from backend.notifications.notifier import notify
        notify(status, {
            "id": doc_id,
            "original_filename": doc["original_filename"],
            "file_hash": file_hash,
            "vendor_name": ext.vendor_name,
            "receipt_date": ext.receipt_date,
            "total_amount": ext.total_amount,
            "currency": ext.currency,
            "category_name": ext.category_name,
            "extraction_confidence": ext.extraction_confidence,
            "review_reason": review_reason,
            "submission_channel": doc["submission_channel"],
            "sender_identifier": doc["sender_identifier"],
        })
    except Exception:
        pass


def _drop_superseded_filed_copy(doc_id: int, previous: str | None, current: str, data_dir: str) -> None:
    """Remove the filed/ copy a reprocess just renamed away from.

    generate_stored_filename builds the name out of receipt_date and
    vendor_receipt_id, both verbatim LLM output, and save_filed has no existence
    guard. So a reprocess that reads the document differently writes a NEW name
    and leaves the old file behind forever. Measured before this existed: 315
    files in filed/ against 310 referenced names.

    CALLED AFTER THE UPDATE HAS COMMITTED, and that ordering is the design:

        save_filed(new) -> UPDATE stored_filename = new -> unlink(old)

    Crash at any point and the worst case is a spare file, which is exactly
    today's behaviour. Reverse the last two and a crash leaves a row naming a
    file that is gone, which verify_backup correctly reports as damage. There is
    a test that asserts the row already carries the new name at unlink time,
    because nothing else would notice the two statements being swapped.

    Never raises, and the whole body is guarded rather than just the unlink.
    This runs AFTER the UPDATE has committed, so the document is already
    processed and recorded; anything that escapes here reaches
    process_document's `except Exception`, which would rewrite status='failed'
    and notify a failure for a document that actually succeeded -- and the queue
    would then reprocess it, which is the very thing that creates orphans. The
    shared-name SELECT below can raise sqlite3.Error ("database is locked")
    under WAL contention with the backup thread, so catching only OSError from
    the unlink is not enough.
    """
    if not previous or previous == current:
        return
    try:
        _remove_superseded(doc_id, previous, data_dir)
    except Exception as e:  # noqa: BLE001 -- tidiness must never fail a document
        logger.warning(
            f"Document {doc_id}: could not remove superseded copy {previous}: {e}"
        )


def _remove_superseded(doc_id: int, previous: str, data_dir: str) -> None:
    """Body of _drop_superseded_filed_copy, split out for readability only.

    Note what the split does NOT buy: the caller wraps this entire call in
    `except Exception`, so a bug in the shared-name SELECT or in the guards
    below is swallowed exactly as it would be inline. That is the intended
    trade -- this runs after the row has committed, and failing loudly here
    would mark a processed document failed and requeue it into the reprocess
    that creates orphans. It fails in the safe direction: an un-removed file is
    an orphan, which Part 2 reports, never a row pointing at nothing.
    """

    # Cannot fire without an 8-hex-prefix collision that also matches on date
    # and receipt id, which file_hash being UNIQUE makes vanishingly unlikely.
    # It is here because the operation is an irreversible delete: this makes it
    # safe by construction rather than safe by a birthday-bound argument that a
    # future change to the filename scheme would silently invalidate.
    with get_connection() as conn:
        shared = conn.execute(
            "SELECT 1 FROM documents WHERE stored_filename = ? AND id != ? LIMIT 1",
            (previous, doc_id),
        ).fetchone()
    if shared:
        logger.info(
            f"Document {doc_id}: keeping {previous}, another row still references it"
        )
        return

    try:
        if remove_filed(previous, data_dir):
            logger.info(f"Document {doc_id}: removed superseded filed copy {previous}")
    except ValueError:
        # A row whose name would leave filed/. Refusing to unlink it is the
        # whole point of the resolver; the document is otherwise fine.
        logger.warning(
            f"Document {doc_id}: previous stored_filename {previous!r} is outside "
            f"filed/; nothing removed"
        )
    except OSError as e:
        logger.warning(f"Document {doc_id}: could not remove {previous}: {e}")


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Cost in USD for a completion, via litellm's own price resolver so the
    number stays correct when the model changes (issue #13). litellm.cost_per_token
    does proper provider resolution (e.g. azure/*, bedrock/*) and RAISES for a
    model it can't map — unlike a naive prefix strip, which can silently land on
    a different-priced registry row. On any miss we fall back to a generic
    $1/$3-per-1M estimate, the same default the old hardcoded table used for
    unknown models. Reasoning tokens are billed by the provider as output tokens,
    so tokens_out already includes them."""
    try:
        import litellm
        prompt_cost, completion_cost = litellm.cost_per_token(model=model, prompt_tokens=tokens_in, completion_tokens=tokens_out)
        if prompt_cost is not None and completion_cost is not None:
            return prompt_cost + completion_cost
    except Exception:
        logger.debug("litellm cost lookup failed for model %s; using fallback", model, exc_info=True)
    return (tokens_in * 1.0 + tokens_out * 3.0) / 1_000_000
