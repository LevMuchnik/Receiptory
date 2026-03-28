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
