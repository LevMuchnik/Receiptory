import json
import pytest
from unittest.mock import patch, MagicMock
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
