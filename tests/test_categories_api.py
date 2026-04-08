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
    assert "Office & Supplies" in names
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
    resp = authed_client.post("/api/categories", json={"name": "Travel"})
    assert resp.status_code == 400


def test_update_category(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "test_cat", "description": "old"})
    cat_id = resp.json()["id"]
    resp = authed_client.patch(f"/api/categories/{cat_id}", json={"description": "new description"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "new description"


def test_list_categories_includes_document_count(authed_client):
    resp = authed_client.get("/api/categories")
    assert resp.status_code == 200
    for c in resp.json():
        assert "document_count" in c
        assert isinstance(c["document_count"], int)


def test_reorder_categories(authed_client):
    r1 = authed_client.post("/api/categories", json={"name": "zzz_first", "description": "first"})
    r2 = authed_client.post("/api/categories", json={"name": "aaa_second", "description": "second"})
    id1 = r1.json()["id"]
    id2 = r2.json()["id"]

    resp = authed_client.patch("/api/categories/reorder", json={
        "order": [
            {"id": id2, "display_order": 0},
            {"id": id1, "display_order": 1},
        ]
    })
    assert resp.status_code == 200

    cats = authed_client.get("/api/categories").json()
    user_cats = [c for c in cats if not c["is_system"]]
    ids_in_order = [c["id"] for c in user_cats]
    assert ids_in_order.index(id2) < ids_in_order.index(id1)


def test_reorder_rejects_system_categories(authed_client):
    resp = authed_client.get("/api/categories")
    system_cat = [c for c in resp.json() if c["is_system"]][0]
    resp = authed_client.patch("/api/categories/reorder", json={
        "order": [{"id": system_cat["id"], "display_order": 0}]
    })
    assert resp.status_code == 400


def test_delete_system_category_fails(authed_client):
    # Get 'pending' system category
    resp = authed_client.get("/api/categories")
    pending = [c for c in resp.json() if c["name"] == "pending"][0]
    resp = authed_client.delete(f"/api/categories/{pending['id']}")
    assert resp.status_code == 400


# === Section tests ===

def test_list_categories_includes_section(authed_client):
    resp = authed_client.get("/api/categories")
    assert resp.status_code == 200
    for c in resp.json():
        if c["is_system"]:
            assert c["section"] is None
        else:
            assert c["section"] in ("expense", "issued", "other")


def test_create_category_with_section_issued(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "Test Issued", "description": "test", "section": "issued"})
    assert resp.status_code == 200
    assert resp.json()["section"] == "issued"


def test_same_name_different_sections_allowed(authed_client):
    resp1 = authed_client.post("/api/categories", json={"name": "Receipt", "section": "expense"})
    assert resp1.status_code == 200
    resp2 = authed_client.post("/api/categories", json={"name": "Receipt", "section": "issued"})
    assert resp2.status_code == 200
    assert resp1.json()["id"] != resp2.json()["id"]


def test_same_name_same_section_rejected(authed_client):
    authed_client.post("/api/categories", json={"name": "Duplicate Test", "section": "expense"})
    resp = authed_client.post("/api/categories", json={"name": "Duplicate Test", "section": "expense"})
    assert resp.status_code == 400


def test_seeded_issued_categories_exist(authed_client):
    resp = authed_client.get("/api/categories")
    issued = [c for c in resp.json() if c["section"] == "issued"]
    issued_names = {c["name"] for c in issued}
    assert "Tax Invoice" in issued_names
    assert "Credit Note" in issued_names
    assert "Receipt (Issued)" in issued_names
    assert len(issued) == 7


def test_system_categories_have_null_section(authed_client):
    resp = authed_client.get("/api/categories")
    system = [c for c in resp.json() if c["is_system"]]
    for c in system:
        assert c["section"] is None


def test_uncategorized_system_category_exists(authed_client):
    """not_a_receipt was renamed to uncategorized in migration 005."""
    resp = authed_client.get("/api/categories")
    names = [c["name"] for c in resp.json() if c["is_system"]]
    assert "uncategorized" in names
    assert "not_a_receipt" not in names
