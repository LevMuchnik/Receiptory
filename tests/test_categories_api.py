import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting


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


def test_list_categories(authed_client):
    resp = authed_client.get("/api/categories")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert "office_supplies" in names
    assert "pending" in names


def test_list_excludes_deleted(authed_client):
    # Create and delete a category
    resp = authed_client.post("/api/categories", json={"name": "temp", "description": "temporary"})
    cat_id = resp.json()["id"]
    authed_client.delete(f"/api/categories/{cat_id}")

    resp = authed_client.get("/api/categories")
    names = [c["name"] for c in resp.json()]
    assert "temp" not in names

    # But include_deleted=true shows it
    resp = authed_client.get("/api/categories?include_deleted=true")
    names = [c["name"] for c in resp.json()]
    assert "temp" in names


def test_create_category(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "insurance", "description": "Insurance premiums"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "insurance"
    assert resp.json()["description"] == "Insurance premiums"


def test_create_duplicate_name_fails(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "travel"})
    assert resp.status_code == 400


def test_update_category(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "test_cat", "description": "old"})
    cat_id = resp.json()["id"]
    resp = authed_client.patch(f"/api/categories/{cat_id}", json={"description": "new description"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "new description"


def test_delete_system_category_fails(authed_client):
    # Get 'pending' system category
    resp = authed_client.get("/api/categories")
    pending = [c for c in resp.json() if c["name"] == "pending"][0]
    resp = authed_client.delete(f"/api/categories/{pending['id']}")
    assert resp.status_code == 400
