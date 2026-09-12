import json
import os
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection


@pytest.fixture
def app(db_path, tmp_data_dir):
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    return create_app(str(tmp_data_dir), run_background=False)


@pytest.fixture
def authed_client(app):
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    return client


def _insert_doc(conn, **overrides):
    defaults = {
        "original_filename": "test.pdf",
        "file_hash": "hash_" + str(id(overrides)),
        "file_size_bytes": 100,
        "status": "processed",
        "submission_channel": "web_upload",
        "vendor_name": "Test Vendor",
        "total_amount": 100.0,
        "receipt_date": "2026-01-15",
        "raw_extracted_text": "some receipt text",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(f"INSERT INTO documents ({cols}) VALUES ({placeholders})", tuple(defaults.values()))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_list_documents(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1")
        _insert_doc(conn, file_hash="h2")
    resp = authed_client.get("/api/documents")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_filter_by_status(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1", status="processed")
        _insert_doc(conn, file_hash="h2", status="failed")
    resp = authed_client.get("/api/documents?status=failed")
    assert resp.json()["total"] == 1
    assert resp.json()["documents"][0]["status"] == "failed"


def test_list_search_fts(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1", vendor_name="Office Depot", raw_extracted_text="office supplies")
        _insert_doc(conn, file_hash="h2", vendor_name="Gas Station", raw_extracted_text="fuel purchase")
    resp = authed_client.get("/api/documents?search=office")
    assert resp.json()["total"] == 1


def test_get_document(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1")
    resp = authed_client.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == doc_id


def test_get_document_not_found(authed_client):
    resp = authed_client.get("/api/documents/999")
    assert resp.status_code == 404


def test_edit_document(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1")
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"vendor_name": "Updated Vendor"})
    assert resp.status_code == 200
    assert resp.json()["vendor_name"] == "Updated Vendor"
    assert resp.json()["manually_edited"] is True

    # Check edit history
    with get_connection() as conn:
        doc = conn.execute("SELECT edit_history FROM documents WHERE id = ?", (doc_id,)).fetchone()
    history = json.loads(doc["edit_history"])
    assert len(history) == 1
    assert history[0]["field"] == "vendor_name"


def test_soft_delete(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1")
    resp = authed_client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    with get_connection() as conn:
        doc = conn.execute("SELECT is_deleted FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert doc["is_deleted"] == 1


def test_reprocess_document(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1", status="processed")
    resp = authed_client.post(f"/api/documents/{doc_id}/reprocess")
    assert resp.status_code == 200
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert doc["status"] == "pending"


def test_list_excludes_deleted(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1", is_deleted=0)
        _insert_doc(conn, file_hash="h2", is_deleted=1)
    resp = authed_client.get("/api/documents")
    assert resp.json()["total"] == 1


def test_list_filter_by_section(authed_client, db_path):
    with get_connection() as conn:
        # Get an expense category and an issued category
        expense_cat = conn.execute("SELECT id FROM categories WHERE section = 'expense' AND is_system = 0 LIMIT 1").fetchone()
        issued_cat = conn.execute("SELECT id FROM categories WHERE section = 'issued' AND is_system = 0 LIMIT 1").fetchone()
        _insert_doc(conn, file_hash="h_expense", category_id=expense_cat["id"])
        _insert_doc(conn, file_hash="h_issued", category_id=issued_cat["id"])

    resp = authed_client.get("/api/documents?section=expense")
    assert resp.json()["total"] == 1
    assert resp.json()["documents"][0]["category_section"] == "expense"

    resp = authed_client.get("/api/documents?section=issued")
    assert resp.json()["total"] == 1
    assert resp.json()["documents"][0]["category_section"] == "issued"


def test_document_response_includes_category_section(authed_client, db_path):
    with get_connection() as conn:
        issued_cat = conn.execute("SELECT id FROM categories WHERE section = 'issued' AND is_system = 0 LIMIT 1").fetchone()
        doc_id = _insert_doc(conn, file_hash="h_section_test", category_id=issued_cat["id"])

    resp = authed_client.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["category_section"] == "issued"


# --- review_reason lifecycle on edit -----------------------------------------
#
# The gate that writes this reason is in the pipeline; these cover what the
# document page does to it afterwards. The first one is the case an API review
# caught: MetadataForm posts every non-empty field on save, so `total_amount` is
# in the body of EVERY save, and an earlier revision cleared the reason whenever
# that key was merely present — a vendor-name typo fix silently erased a totals
# warning nobody had answered.

_MISMATCH = {"subtotal": 623.73, "tax_amount": 112.27, "total_amount": 824.00}
_REASON = "Totals disagree: subtotal 623.73 + tax 112.27 = 736.00, but total reads 824.00 (+88.00). Check for a tip, shipping or discount line."


def test_editing_another_field_keeps_a_still_valid_totals_reason(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr1", status="needs_review", review_reason=_REASON, **_MISMATCH)
    # Exactly what the metadata form sends: the edited field PLUS the unchanged numbers.
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"vendor_name": "Naya", **_MISMATCH})
    assert resp.status_code == 200
    assert resp.json()["review_reason"] == _REASON, "the numbers still disagree; the warning must survive"
    assert resp.json()["status"] == "needs_review"


def test_correcting_the_total_clears_the_reason(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr2", status="needs_review", review_reason=_REASON, **_MISMATCH)
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"total_amount": 736.00})
    assert resp.status_code == 200
    assert resp.json()["total_amount"] == 736.00
    assert resp.json()["review_reason"] is None


def test_correcting_the_total_to_another_wrong_number_rewrites_the_reason(authed_client, db_path):
    # Blanking here would leave the document in needs_review with no explanation,
    # which is the exact state the column exists to abolish.
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr3", status="needs_review", review_reason=_REASON, **_MISMATCH)
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"total_amount": 800.00})
    assert resp.status_code == 200
    reason = resp.json()["review_reason"]
    assert reason is not None and "800.00" in reason and "824.00" not in reason


def test_subtotal_and_tax_edits_actually_reach_the_database(authed_client, db_path):
    # These were absent from DocumentUpdate, so Pydantic's extra='ignore' dropped
    # them: the form posted a correction, the API answered 200, and the value
    # never changed. The reason quotes both numbers, so they have to be editable.
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr4", status="needs_review", review_reason=_REASON, **_MISMATCH)
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"subtotal": 711.73, "tax_amount": 112.27})
    assert resp.status_code == 200
    assert resp.json()["subtotal"] == 711.73
    assert resp.json()["review_reason"] is None, "711.73 + 112.27 = 824.00 now agrees with the total"


def test_approving_clears_the_reason_and_records_it_in_history(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr5", status="needs_review", review_reason=_REASON, **_MISMATCH)
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"status": "processed"})
    assert resp.status_code == 200
    assert resp.json()["review_reason"] is None
    # The audit trail has to show the warning being dismissed, not just vanish.
    fields = [h["field"] for h in resp.json()["edit_history"]]
    assert "review_reason" in fields


def test_reprocess_clears_the_reason(authed_client, db_path):
    # A re-run recomputes the reason from scratch; carrying the old sentence
    # into a pending document would describe numbers that no longer exist.
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr6", status="needs_review", review_reason=_REASON, **_MISMATCH)
    # Assert the status too: a wrong path 404s and the row keeps its old
    # reason, which reads as "the endpoint failed to clear it".
    assert authed_client.post(f"/api/documents/{doc_id}/reprocess").status_code == 200
    with get_connection() as conn:
        row = conn.execute("SELECT status, review_reason FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["review_reason"] is None


def test_batch_reprocess_by_ids_clears_the_reason(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="rr7", status="needs_review", review_reason=_REASON, **_MISMATCH)
    resp = authed_client.post("/api/documents/batch-reprocess", json={"document_ids": [doc_id]})
    assert resp.status_code == 200
    with get_connection() as conn:
        row = conn.execute("SELECT review_reason FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row["review_reason"] is None
