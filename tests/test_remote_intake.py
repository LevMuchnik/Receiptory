import asyncio
import json
import logging
from urllib.parse import unquote
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from PIL import Image

from backend.config import set_setting
from backend.database import get_connection


def _jpeg_bytes(tmp_path):
    file_path = tmp_path / "receipt.jpg"
    Image.new("RGB", (8, 8), color=(240, 240, 240)).save(file_path, "JPEG")
    return file_path.read_bytes()


def _enable_remote_intake(
    *,
    base_url="https://queue.example.test",
    token="dedicated-test-token",
    max_bytes=20 * 1024 * 1024,
):
    set_setting("remote_intake_enabled", True)
    set_setting("remote_intake_base_url", base_url)
    set_setting("remote_intake_token", token)
    set_setting("remote_intake_batch_size", 10)
    set_setting("remote_intake_max_file_bytes", max_bytes)


@pytest.mark.asyncio
async def test_disabled_remote_intake_makes_no_request(db_path, tmp_data_dir):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await poll_remote_intake_once(data_dir=str(tmp_data_dir), client=client)
            == 0
        )
    assert called is False


@pytest.mark.asyncio
async def test_incomplete_remote_intake_config_is_safe(
    db_path, tmp_data_dir, caplog
):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    set_setting("remote_intake_enabled", True)
    set_setting("remote_intake_base_url", "https://queue.example.test")
    set_setting("remote_intake_token", "")
    with caplog.at_level(logging.WARNING):
        assert await poll_remote_intake_once(data_dir=str(tmp_data_dir)) == 0
    assert "token" in caplog.text.lower()


@pytest.mark.parametrize(
    ("raw", "valid"),
    [
        ("https://queue.example.test", True),
        ("https://queue.example.test/prefix/", True),
        ("http://localhost:3000", True),
        ("http://127.0.0.1:3000", True),
        ("http://[::1]:3000", True),
        ("http://queue.example.test", False),
        ("https://queue.example.test/path?token=no", False),
        ("https://queue.example.test/path#fragment", False),
        ("https://user:pass@queue.example.test", False),
    ],
)
def test_validate_remote_base_url(raw, valid):
    from backend.ingestion.remote_intake import validate_remote_base_url

    if valid:
        assert validate_remote_base_url(raw).endswith(
            raw.rstrip("/").split("://", 1)[1]
        )
    else:
        with pytest.raises(ValueError):
            validate_remote_base_url(raw)


@pytest.mark.asyncio
async def test_successful_remote_intake_cycle(db_path, tmp_data_dir):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    _enable_remote_intake()
    content = _jpeg_bytes(tmp_data_dir)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer dedicated-test-token"
        if request.url.path == "/v1/receiptory/items":
            assert request.url.params["limit"] == "10"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "worker/item 1",
                            "filename": "receipt.jpg",
                            "content_type": "image/jpeg",
                            "sender_identifier": "private-worker-reference",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/content"):
            assert unquote(request.url.path).endswith(
                "/items/worker/item 1/content"
            )
            assert b"worker%2Fitem%201" in request.url.raw_path
            return httpx.Response(
                200, content=content, headers={"Content-Type": "image/jpeg"}
            )
        if request.url.path.endswith("/ack"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        acknowledged = await poll_remote_intake_once(
            data_dir=str(tmp_data_dir), client=client
        )

    assert acknowledged == 1
    ack = requests[-1]
    assert ack.method == "POST"
    payload = json.loads(ack.content)
    assert payload["status"] == "accepted"
    assert payload["document_id"] > 0
    with get_connection() as conn:
        document = conn.execute("SELECT * FROM documents").fetchone()
    assert document["submission_channel"] == "remote_intake"
    assert document["sender_identifier"] == "private-worker-reference"
    assert document["status"] == "pending"


@pytest.mark.asyncio
async def test_lost_ack_redelivery_becomes_duplicate(db_path, tmp_data_dir):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    _enable_remote_intake()
    content = _jpeg_bytes(tmp_data_dir)
    acknowledgements = []

    def handler(request):
        if request.url.path == "/v1/receiptory/items":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "stable-id",
                            "filename": "receipt.jpg",
                            "content_type": "image/jpeg",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=content)
        if request.url.path.endswith("/ack"):
            acknowledgements.append(json.loads(request.content))
            if len(acknowledgements) == 1:
                return httpx.Response(503)
            return httpx.Response(200)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await poll_remote_intake_once(data_dir=str(tmp_data_dir), client=client)
            == 0
        )
        assert (
            await poll_remote_intake_once(data_dir=str(tmp_data_dir), client=client)
            == 1
        )

    assert acknowledgements[0]["status"] == "accepted"
    assert acknowledgements[1]["status"] == "duplicate"
    assert (
        acknowledgements[0]["document_id"]
        == acknowledgements[1]["document_id"]
    )
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "body", "max_bytes", "detail"),
    [
        ("receipt.exe", "application/octet-stream", b"bad", 100, "unsupported"),
        ("receipt.jpg", "image/jpeg", b"", 100, "empty"),
        ("receipt.jpg", "image/jpeg", b"x" * 101, 100, "maximum"),
        ("receipt.png", "image/jpeg", b"not-a-jpeg", 100, "extension"),
        ("receipt.jpg", "image/jpeg", b"not-a-jpeg", 100, "content"),
    ],
)
async def test_permanent_rejections_are_acknowledged(
    db_path,
    tmp_data_dir,
    filename,
    content_type,
    body,
    max_bytes,
    detail,
):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    _enable_remote_intake(max_bytes=max_bytes)
    ack_payload = None

    def handler(request):
        nonlocal ack_payload
        if request.url.path == "/v1/receiptory/items":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "rejected",
                            "filename": filename,
                            "content_type": content_type,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=body)
        if request.url.path.endswith("/ack"):
            ack_payload = json.loads(request.content)
            return httpx.Response(200)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await poll_remote_intake_once(data_dir=str(tmp_data_dir), client=client)
            == 1
        )
    assert ack_payload["status"] == "rejected"
    assert detail in ack_payload["detail"].lower()
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_malformed_list_is_not_acknowledged(db_path, tmp_data_dir):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    _enable_remote_intake()

    def handler(request):
        return httpx.Response(200, json={"items": [{"id": 1}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await poll_remote_intake_once(data_dir=str(tmp_data_dir), client=client)
            == 0
        )


@pytest.mark.asyncio
async def test_auth_failure_and_logs_do_not_leak_secrets(
    db_path, tmp_data_dir, caplog
):
    from backend.ingestion.remote_intake import (
        RemoteIntakeAuthError,
        poll_remote_intake_once,
    )

    _enable_remote_intake(token="never-log-this-token")
    set_setting("remote_intake_base_url", "https://queue.example.test")

    def handler(request):
        return httpx.Response(401)

    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(RemoteIntakeAuthError):
                await poll_remote_intake_once(
                    data_dir=str(tmp_data_dir), client=client
                )
    assert "never-log-this-token" not in caplog.text


@pytest.mark.asyncio
async def test_list_timeout_is_transient(db_path, tmp_data_dir):
    from backend.ingestion.remote_intake import poll_remote_intake_once

    _enable_remote_intake()

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await poll_remote_intake_once(
                data_dir=str(tmp_data_dir), client=client
            )


def test_remote_intake_backoff_is_exponential_and_capped():
    from backend.ingestion.remote_intake import _backoff_delay

    assert _backoff_delay(1, 10, jitter=0) == 10
    assert _backoff_delay(2, 10, jitter=0) == 20
    assert _backoff_delay(10, 10, jitter=0) == 300
    assert _backoff_delay(10, 10, jitter=1) == 300


@pytest.mark.asyncio
async def test_remote_intake_poller_backoff_resets_after_success(
    db_path, tmp_data_dir
):
    from backend.ingestion.remote_intake import run_remote_intake_poller

    timeout = httpx.ReadTimeout("temporary queue timeout")
    side_effects = [timeout, timeout, 1, timeout, asyncio.CancelledError()]
    with (
        patch(
            "backend.ingestion.remote_intake.poll_remote_intake_once",
            new=AsyncMock(side_effect=side_effects),
        ),
        patch(
            "backend.ingestion.remote_intake.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
        patch("backend.ingestion.remote_intake.random.uniform", return_value=0),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_remote_intake_poller(str(tmp_data_dir))

    assert [call.args[0] for call in sleep.await_args_list] == [10, 20, 10, 10]


@pytest.mark.asyncio
async def test_remote_intake_poller_cancellation_is_immediate(
    db_path, tmp_data_dir
):
    from backend.ingestion.remote_intake import run_remote_intake_poller

    with (
        patch(
            "backend.ingestion.remote_intake.poll_remote_intake_once",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch(
            "backend.ingestion.remote_intake.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_remote_intake_poller(str(tmp_data_dir))
    sleep.assert_not_awaited()


def test_app_lifespan_starts_remote_intake_poller(db_path, tmp_data_dir):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    started = []

    async def fake_remote_poller(data_dir):
        started.append(data_dir)
        await asyncio.Future()

    with patch(
        "backend.ingestion.remote_intake.run_remote_intake_poller",
        new=fake_remote_poller,
    ):
        app = create_app(str(tmp_data_dir), run_background=True)
        with TestClient(app):
            pass

    assert started == [str(tmp_data_dir)]
