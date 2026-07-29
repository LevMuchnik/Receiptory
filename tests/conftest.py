import json
import os
import tempfile
import pytest
from unittest.mock import MagicMock
from backend.database import init_db, get_connection

@pytest.fixture
def tmp_data_dir(tmp_path):
    for subdir in ["storage/originals", "storage/converted", "storage/filed", "storage/page_cache", "logs"]:
        (tmp_path / subdir).mkdir(parents=True)
    return tmp_path

@pytest.fixture(autouse=True)
def _reset_test_env(monkeypatch):
    """Reset database global and clear RECEIPTORY_ env vars for test isolation.
    litellm auto-loads .env on import, and dotenv.load_dotenv in create_app
    also loads .env. We set vars to empty string (not delete) so load_dotenv
    with override=False won't re-populate them.

    Clearing only the vars *currently* in os.environ is not enough: create_app's
    later load_dotenv pulls any not-yet-present key (e.g. RECEIPTORY_AUTH_PASSWORD)
    straight from a developer's real .env, breaking login/settings in isolation.
    So blank the full known keyset up front — every DEFAULTS-derived var plus the
    special-cased RECEIPTORY_AUTH_PASSWORD — regardless of current presence."""
    import backend.database as _db_mod
    from backend.config import DEFAULTS
    _db_mod._db_path = None
    known = {f"RECEIPTORY_{k.upper()}" for k in DEFAULTS} | {"RECEIPTORY_AUTH_PASSWORD"}
    for key in known | {k for k in os.environ if k.startswith("RECEIPTORY_")}:
        monkeypatch.setenv(key, "")
    # Ensure DEV mode is on for tests (prevents static file mount from intercepting API routes)
    monkeypatch.setenv("RECEIPTORY_DEV", "1")
    yield
    _db_mod._db_path = None


@pytest.fixture
def db_path(tmp_data_dir):
    path = str(tmp_data_dir / "receiptory.db")
    init_db(path)
    return path

@pytest.fixture
def db_conn(db_path):
    with get_connection() as conn:
        yield conn

@pytest.fixture
def sample_pdf_path(tmp_path):
    import fitz

    path = tmp_path / "synthetic-receipt.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text(
        (72, 72),
        "Synthetic Receipt\nVendor: Example Office\nTotal: 42.00 USD",
        fontsize=12,
    )
    document.save(path)
    document.close()
    return str(path)


SAMPLE_LLM_RESPONSE = json.dumps({
    "receipt_date": "2026-01-15", "document_title": "Tax Invoice", "vendor_name": "Office Depot",
    "vendor_tax_id": "515234567", "vendor_receipt_id": "INV-2026-001", "client_name": None, "client_tax_id": None,
    "description": "Office supplies purchase",
    "line_items": [{"description": "Paper A4", "quantity": 5, "unit_price": 25.0}, {"description": "Ink cartridge", "quantity": 2, "unit_price": 89.0}],
    "subtotal": 303.0, "tax_amount": 51.51, "total_amount": 354.51, "currency": "ILS",
    "payment_method": "credit_card", "payment_identifier": "4580", "language": "he",
    "additional_fields": [], "raw_extracted_text": "Office Depot\nTax Invoice\n...",
    "document_type": "expense_receipt", "category": "office_supplies", "extraction_confidence": 0.95,
})


def mock_llm_response(content=SAMPLE_LLM_RESPONSE, finish_reason="stop"):
    """Build a litellm-shaped mock response for extract_document tests."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.choices[0].finish_reason = finish_reason
    mock_response.usage.prompt_tokens = 1000
    mock_response.usage.completion_tokens = 500
    return mock_response
