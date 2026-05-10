import io
import json
import os

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.config import init_settings, set_setting
from backend.main import create_app

# Tiny valid JPEG (1x1 white) for upload payloads.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605"
    "08070707090908"
    "0a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c"
    "2837292c30313434"
    "1f27393d38323c2e333432ffc0000b08000100010101"
    "1100ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffc400b5100002010303020403050504040000017d010203000411051221314106135161"
    "07227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a"
    "3435363738393a434445464748494a535455565758595a636465666768696a73747576"
    "7778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6"
    "b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3"
    "f4f5f6f7f8f9faffda0008010100003f00fb0fffd9"
)


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


def _post_frame(client: TestClient, **extra) -> dict:
    files = {"file": ("frame.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")}
    data = {"width": "1280", "height": "720", **extra}
    resp = client.post("/api/scanner/test-frames", files=files, data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_post_and_list_test_frame(authed_client, tmp_data_dir):
    body = _post_frame(authed_client, detector_name="classical")
    assert "id" in body
    assert body["frame_path"].startswith("scanner_test_set/")
    abs_path = os.path.join(str(tmp_data_dir), body["frame_path"])
    assert os.path.exists(abs_path)

    resp = authed_client.get("/api/scanner/test-frames")
    assert resp.status_code == 200
    frames = resp.json()["frames"]
    assert any(f["id"] == body["id"] for f in frames)


def test_get_image_endpoint(authed_client):
    body = _post_frame(authed_client)
    resp = authed_client.get(f"/api/scanner/test-frames/{body['id']}/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
    assert resp.content == TINY_JPEG


def test_patch_ground_truth_and_notes(authed_client):
    body = _post_frame(authed_client)
    gt = json.dumps({"topLeft": {"x": 0, "y": 0}, "topRight": {"x": 1, "y": 0},
                     "bottomRight": {"x": 1, "y": 1}, "bottomLeft": {"x": 0, "y": 1}})
    resp = authed_client.patch(
        f"/api/scanner/test-frames/{body['id']}",
        json={"ground_truth_json": gt, "notes": "white-on-white case"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 1}

    frames = authed_client.get("/api/scanner/test-frames").json()["frames"]
    row = next(f for f in frames if f["id"] == body["id"])
    assert row["ground_truth_json"] == gt
    assert row["notes"] == "white-on-white case"


def test_delete_test_frame(authed_client, tmp_data_dir):
    body = _post_frame(authed_client)
    abs_path = os.path.join(str(tmp_data_dir), body["frame_path"])
    assert os.path.exists(abs_path)

    resp = authed_client.delete(f"/api/scanner/test-frames/{body['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": body["id"]}
    assert not os.path.exists(abs_path)

    resp = authed_client.get(f"/api/scanner/test-frames/{body['id']}/image")
    assert resp.status_code == 404


def test_test_frames_require_auth(app):
    client = TestClient(app)
    resp = client.get("/api/scanner/test-frames")
    assert resp.status_code == 401
    files = {"file": ("frame.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")}
    resp = client.post("/api/scanner/test-frames", files=files,
                       data={"width": "1", "height": "1"})
    assert resp.status_code == 401


def test_active_config_get_default(authed_client):
    resp = authed_client.get("/api/scanner/active-config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["detector"] == "classical"
    assert isinstance(cfg["params"], dict)


def test_active_config_put_roundtrip(authed_client):
    new_cfg = {"detector": "classical", "params": {"shadowNorm": False, "wArea": 0.5}}
    resp = authed_client.put("/api/scanner/active-config", json=new_cfg)
    assert resp.status_code == 200
    resp = authed_client.get("/api/scanner/active-config")
    assert resp.json() == new_cfg


def test_active_config_requires_auth(app):
    client = TestClient(app)
    assert client.get("/api/scanner/active-config").status_code == 401
    assert client.put("/api/scanner/active-config",
                      json={"detector": "classical", "params": {}}).status_code == 401
