import os
import json
import pytest
import bcrypt
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
from backend.processing.extract import ExtractionResult, LLMExtractionResult


MOCK_EXTRACTION = ExtractionResult(
    receipt_date="2026-01-15",
    document_title="Tax Invoice",
    vendor_name="Office Depot",
    vendor_tax_id="515234567",
    vendor_receipt_id="INV-001",
    description="Office supplies",
    line_items=[{"description": "Paper", "quantity": 1, "unit_price": 25.0}],
    subtotal=25.0,
    tax_amount=4.25,
    total_amount=29.25,
    currency="ILS",
    payment_method="credit_card",
    payment_identifier="4580",
    language="he",
    additional_fields=[],
    raw_extracted_text="Office Depot Tax Invoice Paper 25.00",
    document_type="expense_receipt",
    category_name="office_supplies",
    extraction_confidence=0.95,
)

MOCK_LLM_RESULT = LLMExtractionResult(
    extraction=MOCK_EXTRACTION,
    tokens_in=1000,
    tokens_out=500,
    model="gemini/gemini-3-flash-preview",
)


@pytest.fixture
def setup(db_path, tmp_data_dir):
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    set_setting("llm_api_key", "test-key")
    app = create_app(str(tmp_data_dir), run_background=False)
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    return client, str(tmp_data_dir)


@patch("backend.processing.pipeline.extract_document")
def test_full_lifecycle(mock_extract, setup, sample_pdf_path):
    """Upload -> Process -> View -> Edit -> Export full lifecycle."""
    client, data_dir = setup
    mock_extract.return_value = MOCK_LLM_RESULT

    # 1. Upload
    with open(sample_pdf_path, "rb") as f:
        resp = client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    doc_id = resp.json()["documents"][0]["id"]

    # 2. Verify pending
    resp = client.get(f"/api/documents/{doc_id}")
    assert resp.json()["status"] == "pending"

    # 3. Process manually (simulating queue)
    from backend.processing.pipeline import process_document
    process_document(doc_id, data_dir)

    # 4. Verify processed
    resp = client.get(f"/api/documents/{doc_id}")
    doc = resp.json()
    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 29.25

    # 5. Edit
    resp = client.patch(f"/api/documents/{doc_id}", json={"vendor_name": "Office Depot Inc."})
    assert resp.json()["vendor_name"] == "Office Depot Inc."
    assert resp.json()["manually_edited"] is True

    # 6. Search
    resp = client.get("/api/documents?search=Office")
    assert resp.json()["total"] >= 1

    # 7. Stats
    resp = client.get("/api/stats/dashboard")
    assert resp.status_code == 200

    # 8. Duplicate upload rejected
    with open(sample_pdf_path, "rb") as f:
        resp = client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert len(resp.json()["duplicates"]) == 1
