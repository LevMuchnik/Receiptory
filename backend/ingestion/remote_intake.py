import asyncio
import logging
import os
import random
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

from backend.config import get_setting
from backend.ingestion.service import ingest_local_file

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = httpx.Timeout(connect=10, read=30, write=30, pool=10)
ALLOWED_MEDIA_TYPES = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}


class RemoteIntakeAuthError(RuntimeError):
    pass


class RemoteItemRejected(ValueError):
    pass


class RemoteItem(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    sender_identifier: str | None = Field(default=None, max_length=512)


class RemoteItemsResponse(BaseModel):
    items: list[RemoteItem]


def validate_remote_base_url(raw: str) -> str:
    """Return a normalized fixed queue base URL or raise a safe ValueError."""
    value = raw.strip()
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        raise ValueError("Remote intake base URL must be absolute")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Remote intake base URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Remote intake base URL cannot contain query or fragment")
    if parsed.scheme == "http":
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote intake base URL must use HTTPS")
    elif parsed.scheme != "https":
        raise ValueError("Remote intake base URL must use HTTPS")
    return value.rstrip("/")


def _raise_for_queue_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise RemoteIntakeAuthError("Remote intake authentication failed")
    response.raise_for_status()


def _item_urls(base_url: str, item_id: str) -> tuple[str, str]:
    encoded_id = quote(item_id, safe="")
    item_base = f"{base_url}/v1/receiptory/items/{encoded_id}"
    return f"{item_base}/content", f"{item_base}/ack"


def _validate_item_metadata(item: RemoteItem) -> tuple[str, str]:
    media_type = item.content_type.split(";", 1)[0].strip().lower()
    extensions = ALLOWED_MEDIA_TYPES.get(media_type)
    if not extensions:
        raise RemoteItemRejected("unsupported media type")
    filename = os.path.basename(item.filename)
    extension = Path(filename).suffix.lower()
    if extension not in extensions:
        raise RemoteItemRejected("filename extension does not match media type")
    return filename, media_type


def _validate_file_signature(file_path: str, media_type: str) -> None:
    with open(file_path, "rb") as file:
        header = file.read(12)
    valid = {
        "application/pdf": header.startswith(b"%PDF-"),
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }[media_type]
    if not valid:
        raise RemoteItemRejected("file content does not match media type")


async def _download_item(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    extension: str,
    media_type: str,
    max_bytes: int,
) -> str:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp:
            temp_path = temp.name
            total = 0
            async with client.stream(
                "GET", url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                _raise_for_queue_status(response)
                for_header = response.headers.get("content-type")
                if for_header:
                    response_type = for_header.split(";", 1)[0].strip().lower()
                    if response_type != media_type:
                        raise RemoteItemRejected(
                            "response media type does not match item metadata"
                        )
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise RemoteItemRejected(
                            "file exceeds configured maximum size"
                        )
                    temp.write(chunk)
        if total == 0:
            raise RemoteItemRejected("empty file")
        _validate_file_signature(temp_path, media_type)
        return temp_path
    except Exception:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


async def _acknowledge(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    payload: dict,
) -> None:
    response = await client.post(
        url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
    )
    _raise_for_queue_status(response)


async def poll_remote_intake_once(
    *,
    data_dir: str,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Process one bounded queue batch and return successful ack count."""
    if not get_setting("remote_intake_enabled"):
        return 0

    raw_base_url = get_setting("remote_intake_base_url") or ""
    token = get_setting("remote_intake_token") or ""
    if not raw_base_url or not token:
        logger.warning(
            "Remote intake is enabled but its base URL or token is not configured"
        )
        return 0

    try:
        base_url = validate_remote_base_url(str(raw_base_url))
    except ValueError:
        logger.warning("Remote intake base URL is invalid")
        return 0

    batch_size = max(1, min(int(get_setting("remote_intake_batch_size") or 10), 100))
    max_bytes = max(1, int(get_setting("remote_intake_max_file_bytes") or 1))
    headers = {"Authorization": f"Bearer {token}"}
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    try:
        list_response = await client.get(
            f"{base_url}/v1/receiptory/items",
            params={"limit": batch_size},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_queue_status(list_response)
        try:
            items = RemoteItemsResponse.model_validate(list_response.json()).items
        except (ValueError, ValidationError):
            logger.warning("Remote intake returned a malformed item list")
            return 0

        acknowledged = 0
        for item in items[:batch_size]:
            content_url, ack_url = _item_urls(base_url, item.id)
            temp_path = None
            try:
                filename, media_type = _validate_item_metadata(item)
                extension = Path(filename).suffix.lower()
                temp_path = await _download_item(
                    client,
                    url=content_url,
                    headers=headers,
                    extension=extension,
                    media_type=media_type,
                    max_bytes=max_bytes,
                )
                result = await asyncio.to_thread(
                    ingest_local_file,
                    temp_path,
                    filename=filename,
                    data_dir=data_dir,
                    submission_channel="remote_intake",
                    sender_identifier=item.sender_identifier,
                )
                await _acknowledge(
                    client,
                    url=ack_url,
                    headers=headers,
                    payload={
                        "status": result.status,
                        "document_id": result.document_id,
                    },
                )
                acknowledged += 1
            except RemoteItemRejected as error:
                try:
                    await _acknowledge(
                        client,
                        url=ack_url,
                        headers=headers,
                        payload={"status": "rejected", "detail": str(error)},
                    )
                    acknowledged += 1
                except RemoteIntakeAuthError:
                    raise
                except httpx.HTTPError:
                    logger.warning(
                        "Remote intake could not acknowledge a rejected item"
                    )
            except RemoteIntakeAuthError:
                raise
            except httpx.HTTPError:
                logger.warning(
                    "Remote intake item request failed; it will be retried"
                )
            except Exception:
                logger.warning(
                    "Remote intake item failed; metadata was omitted from logs"
                )
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
        return acknowledged
    finally:
        if owns_client:
            await client.aclose()


def _backoff_delay(
    failure_count: int,
    poll_interval: int,
    *,
    jitter: float | None = None,
) -> float:
    base = min(300.0, float(max(1, poll_interval)) * (2 ** (failure_count - 1)))
    if jitter is None:
        jitter = random.uniform(0, min(5.0, base * 0.25))
    return min(300.0, base + max(0.0, jitter))


async def run_remote_intake_poller(data_dir: str) -> None:
    """Poll forever with bounded backoff until application cancellation."""
    failures = 0
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        while True:
            poll_interval = max(
                1, int(get_setting("remote_intake_poll_interval_seconds") or 10)
            )
            try:
                await poll_remote_intake_once(data_dir=data_dir, client=client)
            except asyncio.CancelledError:
                raise
            except RemoteIntakeAuthError:
                failures += 1
                delay = _backoff_delay(failures, poll_interval)
                logger.warning(
                    "Remote intake authentication failed; polling will retry"
                )
            except httpx.HTTPError:
                failures += 1
                delay = _backoff_delay(failures, poll_interval)
                logger.warning(
                    "Remote intake queue request failed; polling will retry"
                )
            except Exception:
                failures += 1
                delay = _backoff_delay(failures, poll_interval)
                logger.warning(
                    "Remote intake polling failed; details were omitted from logs"
                )
            else:
                failures = 0
                delay = poll_interval
            await asyncio.sleep(delay)
