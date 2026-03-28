import io
import csv
import zipfile
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
import shutil
import os


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


@pytest.fixture
def docs_with_files(authed_client, db_path, tmp_data_dir, sample_pdf_path):
    """Create documents with actual filed PDFs."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")
    os.makedirs(filed_dir, exist_ok=True)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('r1.pdf', 'h1', 100, 'processed', '2026-01-15-INV001-h1.pdf', '2026-01-15', 'Vendor A', 100.0, 4, 'web_upload')"""
        )
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('r2.pdf', 'h2', 200, 'processed', '2026-01-20-INV002-h2.pdf', '2026-01-20', 'Vendor B', 200.0, 5, 'web_upload')"""
        )

    # Create filed PDFs
    shutil.copy2(sample_pdf_path, os.path.join(filed_dir, "2026-01-15-INV001-h1.pdf"))
    shutil.copy2(sample_pdf_path, os.path.join(filed_dir, "2026-01-20-INV002-h2.pdf"))


def test_export_zip_structure(authed_client, docs_with_files):
    resp = authed_client.post("/api/export", json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("metadata.csv" in n for n in names)
    assert any(".pdf" in n for n in names)


def test_export_csv_content(authed_client, docs_with_files):
    resp = authed_client.post("/api/export", json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_content = zf.read("metadata.csv").decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["vendor_name"] in ("Vendor A", "Vendor B")


def test_export_updates_last_exported(authed_client, docs_with_files):
    authed_client.post("/api/export", json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    with get_connection() as conn:
        docs = conn.execute("SELECT last_exported_date FROM documents WHERE status = 'processed'").fetchall()
    for doc in docs:
        assert doc["last_exported_date"] is not None
