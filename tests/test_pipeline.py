import json
import pytest
from unittest.mock import patch
from backend.processing.pipeline import process_document
from backend.database import get_connection
from backend.config import set_setting, init_settings
from backend.processing.extract import ExtractionResult, LLMExtractionResult

MOCK_EXTRACTION = ExtractionResult(receipt_date="2026-01-15", document_title="Tax Invoice", vendor_name="Office Depot", vendor_tax_id="515234567", vendor_receipt_id="INV-001", description="Office supplies", line_items=[{"description": "Paper", "quantity": 1, "unit_price": 25.0}], subtotal=25.0, tax_amount=4.25, total_amount=29.25, currency="ILS", payment_method="credit_card", payment_identifier="4580", language="he", additional_fields=[], raw_extracted_text="Office Depot Tax Invoice ...", document_type="expense_receipt", category_name="office_supplies", extraction_confidence=0.95)

MOCK_LLM_RESULT = LLMExtractionResult(extraction=MOCK_EXTRACTION, tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")

@pytest.fixture
def setup_db(db_path, tmp_data_dir):
    init_settings()
    set_setting("llm_api_key", "test-key")
    return str(tmp_data_dir)

@pytest.fixture
def pending_doc(setup_db, sample_pdf_path):
    import shutil
    from backend.storage import compute_file_hash, save_original
    file_hash = compute_file_hash(sample_pdf_path)
    save_original(sample_pdf_path, file_hash, ".pdf", setup_db)
    with get_connection() as conn:
        conn.execute("INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel) VALUES (?, ?, ?, 'pending', 'web_upload')", ("test.pdf", file_hash, 1234))
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return doc_id

@patch("backend.processing.pipeline.extract_document")
def test_process_document_success(mock_extract, pending_doc, setup_db):
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 29.25
    assert doc["extraction_confidence"] == 0.95
    assert doc["processing_model"] == "gemini/gemini-3-flash-preview"
    assert doc["processing_tokens_in"] == 1000
    assert doc["stored_filename"] is not None

@patch("backend.processing.pipeline.extract_document")
def test_process_document_low_confidence(mock_extract, pending_doc, setup_db):
    low_conf = LLMExtractionResult(extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "extraction_confidence": 0.3}), tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = low_conf
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "needs_review"

@patch("backend.processing.pipeline.extract_document")
def test_process_document_failure(mock_extract, pending_doc, setup_db):
    mock_extract.side_effect = Exception("LLM timeout")
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, processing_error, processing_attempts FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "failed"
    assert "LLM timeout" in doc["processing_error"]
    assert doc["processing_attempts"] == 1

@patch("backend.processing.pipeline.extract_document")
def test_process_document_type_override(mock_extract, pending_doc, setup_db):
    set_setting("business_tax_ids", ["515234567"])
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT document_type FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["document_type"] == "issued_invoice"


@patch("backend.processing.pipeline.extract_document")
def test_process_document_missing_confidence_needs_review(mock_extract, pending_doc, setup_db):
    # Missing confidence is not full confidence — route to a human.
    no_conf = LLMExtractionResult(extraction=ExtractionResult(**{**MOCK_EXTRACTION.__dict__, "extraction_confidence": None}), tokens_in=1000, tokens_out=500, model="gemini/gemini-3-flash-preview")
    mock_extract.return_value = no_conf
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "needs_review"


@patch("backend.processing.pipeline.extract_document")
def test_process_document_threads_json_mode_setting(mock_extract, pending_doc, setup_db):
    set_setting("llm_json_mode", False)
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    assert mock_extract.call_args.kwargs["json_mode"] is False


@patch("backend.processing.pipeline.extract_document")
def test_process_document_threads_parse_retries_setting(mock_extract, pending_doc, setup_db):
    # Issue #12: the pipeline must forward llm_parse_retries into extract_document.
    # A future edit dropping the kwarg would silently revert to 0 retries — this
    # one-line assertion catches that wiring regression.
    set_setting("llm_parse_retries", 3)
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    assert mock_extract.call_args.kwargs["parse_retries"] == 3


@patch("backend.processing.pipeline.extract_document")
def test_process_document_threads_reasoning_effort_setting(mock_extract, pending_doc, setup_db):
    # Issue #13: the pipeline must forward llm_reasoning_effort into
    # extract_document. Dropping the kwarg would silently revert to "none".
    set_setting("llm_reasoning_effort", "high")
    mock_extract.return_value = MOCK_LLM_RESULT
    process_document(pending_doc, setup_db)
    assert mock_extract.call_args.kwargs["reasoning_effort"] == "high"


def test_estimate_cost_uses_registry():
    from backend.processing.pipeline import estimate_cost
    # gpt-4o is $2.50 / $10.00 per 1M in litellm's registry.
    assert estimate_cost("gpt-4o", 1000, 2000) == pytest.approx((1000 * 2.50 + 2000 * 10.00) / 1_000_000)


def test_estimate_cost_strips_provider_prefix():
    from backend.processing.pipeline import estimate_cost
    # Provider-prefixed id must resolve to the same cost as the bare id.
    assert estimate_cost("gemini/gemini-3-flash-preview", 1000, 2000) == pytest.approx(
        estimate_cost("gemini-3-flash-preview", 1000, 2000)
    )
    assert estimate_cost("gemini/gemini-3-flash-preview", 1000, 2000) > 0


def test_estimate_cost_unknown_model_falls_back():
    from backend.processing.pipeline import estimate_cost
    # Unknown model -> generic $1 / $3 per 1M, matching the old default tuple.
    assert estimate_cost("totally/unknown-model", 1000, 2000) == pytest.approx((1000 * 1.0 + 2000 * 3.0) / 1_000_000)


@patch("backend.processing.extract.litellm_completion")
def test_process_document_with_trailing_junk_response(mock_completion, pending_doc, setup_db):
    # End-to-end regression for issue #10 (docs #70/#215): the LLM emits a
    # complete JSON object then keeps generating. The document must end up
    # 'processed', not 'failed' — this patches the raw LLM call so the real
    # parse ladder runs inside the pipeline.
    from tests.conftest import SAMPLE_LLM_RESPONSE, mock_llm_response
    mock_completion.return_value = mock_llm_response(content=SAMPLE_LLM_RESPONSE + "\n\nAs requested, all fields were extracted from the document image.")
    process_document(pending_doc, setup_db)
    with get_connection() as conn:
        doc = conn.execute("SELECT status, vendor_name, total_amount, processing_error FROM documents WHERE id = ?", (pending_doc,)).fetchone()
    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 354.51
    assert doc["processing_error"] is None
