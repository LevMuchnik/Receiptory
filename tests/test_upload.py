import os
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
import bcrypt


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


def test_upload_pdf(authed_client, sample_pdf_path):
    """Uploading a PDF creates a pending document."""
    with open(sample_pdf_path, "rb") as f:
        resp = authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["documents"]) == 1
    assert data["documents"][0]["status"] == "pending"


def test_upload_duplicate_rejected(authed_client, sample_pdf_path):
    """Uploading the same file twice rejects the duplicate."""
    with open(sample_pdf_path, "rb") as f:
        authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    with open(sample_pdf_path, "rb") as f:
        resp = authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    assert len(resp.json()["duplicates"]) == 1


def test_upload_requires_auth(app, sample_pdf_path):
    """Upload without auth returns 401."""
    client = TestClient(app)
    with open(sample_pdf_path, "rb") as f:
        resp = client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 401


def test_upload_stores_original(authed_client, sample_pdf_path, tmp_data_dir):
    """Upload copies the original file to storage."""
    with open(sample_pdf_path, "rb") as f:
        resp = authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    doc = resp.json()["documents"][0]
    original_path = os.path.join(str(tmp_data_dir), "storage", "originals", f"{doc['file_hash']}.pdf")
    assert os.path.exists(original_path)
