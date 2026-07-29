import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, sleep
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from PIL import Image

from backend.config import set_setting
from backend.database import get_connection
from backend.main import create_app


class _QueueState:
    content = b""
    token = ""
    acknowledgements = []


class _QueueHandler(BaseHTTPRequestHandler):
    def _authorized(self):
        return self.headers.get("Authorization") == f"Bearer {_QueueState.token}"

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        path = urlsplit(self.path).path
        if path == "/v1/receiptory/items":
            self._json(
                200,
                {
                    "items": [
                        {
                            "id": "e2e-item",
                            "filename": "worker-receipt.jpg",
                            "content_type": "image/jpeg",
                            "sender_identifier": "private-e2e-worker",
                        }
                    ]
                },
            )
            return
        if path == "/v1/receiptory/items/e2e-item/content":
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(_QueueState.content)))
            self.end_headers()
            self.wfile.write(_QueueState.content)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if urlsplit(self.path).path != "/v1/receiptory/items/e2e-item/ack":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        _QueueState.acknowledgements.append(
            json.loads(self.rfile.read(length).decode())
        )
        self._json(200, {"ok": True})

    def log_message(self, format, *args):
        pass


def test_remote_queue_to_database_and_ack(db_path, tmp_data_dir):
    image_path = tmp_data_dir / "queue-receipt.jpg"
    Image.new("RGB", (8, 8), color=(220, 220, 220)).save(image_path, "JPEG")
    _QueueState.content = image_path.read_bytes()
    _QueueState.token = "private-e2e-token"
    _QueueState.acknowledgements = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), _QueueHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    set_setting("remote_intake_enabled", True)
    set_setting(
        "remote_intake_base_url",
        f"http://127.0.0.1:{server.server_address[1]}",
    )
    set_setting("remote_intake_token", _QueueState.token)
    set_setting("remote_intake_poll_interval_seconds", 1)

    try:
        app = create_app(str(tmp_data_dir), run_background=True)
        with TestClient(app):
            deadline = monotonic() + 5
            while not _QueueState.acknowledgements and monotonic() < deadline:
                sleep(0.05)
            assert _QueueState.acknowledgements
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    acknowledgement = _QueueState.acknowledgements[0]
    assert acknowledgement["status"] == "accepted"
    with get_connection() as conn:
        document = conn.execute(
            "SELECT * FROM documents WHERE id = ?",
            (acknowledgement["document_id"],),
        ).fetchone()
    assert document["submission_channel"] == "remote_intake"
    assert document["sender_identifier"] == "private-e2e-worker"
    original = (
        tmp_data_dir
        / "storage"
        / "originals"
        / f"{document['file_hash']}.jpg"
    )
    assert original.read_bytes() == _QueueState.content

    log_text = (tmp_data_dir / "logs" / "receiptory.log").read_text()
    assert _QueueState.token not in log_text
    assert "private-e2e-worker" not in log_text
