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


def test_dashboard_stats(authed_client, db_path):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, total_amount, category_id, submission_channel)
               VALUES ('test.pdf', 'h1', 100, 'processed', 50.0, 4, 'web_upload')"""
        )
    resp = authed_client.get("/api/stats/dashboard")
    assert resp.status_code == 200
    assert "processed_this_month" in resp.json()


def test_processing_costs(authed_client, db_path):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, processing_model, processing_tokens_in, processing_tokens_out, processing_cost_usd, submission_channel)
               VALUES ('test.pdf', 'h1', 100, 'processed', 'gemini/gemini-3-flash-preview', 1000, 500, 0.001, 'web_upload')"""
        )
    resp = authed_client.get("/api/stats/processing-costs")
    assert resp.status_code == 200
    assert resp.json()["total_cost_usd"] > 0
