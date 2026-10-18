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


def _expense(conn, h, **kw):
    row = {"original_filename": "x.pdf", "file_hash": h, "file_size_bytes": 1, "status": "processed",
           "submission_channel": "web_upload", "category_id": 4, "document_type": "expense_receipt"}
    row.update(kw)
    conn.execute(f"INSERT INTO documents ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))


def test_dashboard_totals_are_in_the_reference_currency(authed_client, db_path):
    """#72: category totals sum converted amounts, never raw amounts across currencies."""
    with get_connection() as conn:
        # ILS without a rate yet still counts at face value (an ILS-only install sees the same totals)
        _expense(conn, "h1", total_amount=50.0, currency="ILS")
        _expense(conn, "h2", total_amount=100.0, currency="USD", conversion_rate=3.0, converted_amount=300.0, conversion_ref="ILS")
        # not convertible yet: excluded and counted
        _expense(conn, "h3", total_amount=999.0, currency="USD")
        _expense(conn, "h4", total_amount=999.0, currency=None)
        # a rate for another reference is stale
        _expense(conn, "h5", total_amount=999.0, currency="EUR", conversion_rate=0.3, converted_amount=299.7, conversion_ref="USD")
    body = authed_client.get("/api/stats/dashboard").json()
    assert body["reference_currency"] == "ILS"
    assert sum(c["total"] for c in body["total_expenses_by_category"]) == 350.0
    assert body["unconverted_count"] == 3


def test_dashboard_ils_totals_unchanged(authed_client, db_path):
    with get_connection() as conn:
        _expense(conn, "h1", total_amount=50.0, currency="ILS")
        _expense(conn, "h2", total_amount=25.5, currency="ILS", conversion_rate=1.0, converted_amount=25.5, conversion_ref="ILS")
    body = authed_client.get("/api/stats/dashboard").json()
    assert sum(c["total"] for c in body["total_expenses_by_category"]) == 75.5
    assert body["unconverted_count"] == 0


def test_dashboard_date_range_still_filters(authed_client, db_path):
    with get_connection() as conn:
        _expense(conn, "h1", total_amount=10.0, currency="ILS", receipt_date="2026-01-10")
        _expense(conn, "h2", total_amount=20.0, currency="ILS", receipt_date="2026-03-10")
    body = authed_client.get("/api/stats/dashboard", params={"date_from": "2026-02-01", "date_to": "2026-12-31"}).json()
    assert sum(c["total"] for c in body["total_expenses_by_category"]) == 20.0
