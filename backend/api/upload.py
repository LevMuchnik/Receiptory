import os
import tempfile
import logging
from fastapi import APIRouter, UploadFile, File, Depends, Request

from backend.auth import require_auth
from backend.database import get_connection
from backend.storage import compute_file_hash, save_original

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    username: str = Depends(require_auth),
):
    """Upload one or more files for processing."""
    data_dir = request.app.state.data_dir
    created = []
    duplicates = []

    for upload in files:
        # Save to temp file to compute hash
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(upload.filename or "")[1]) as tmp:
            content = await upload.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            file_hash = compute_file_hash(tmp_path)
            file_size = len(content)
            ext = os.path.splitext(upload.filename or ".pdf")[1].lower()

            # Check for duplicate
            with get_connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
                ).fetchone()

            if existing:
                duplicates.append({
                    "filename": upload.filename,
                    "file_hash": file_hash,
                    "existing_id": existing["id"],
                })
                continue

            # Save original file
            save_original(tmp_path, file_hash, ext, data_dir)

            # Create document record
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel)
                       VALUES (?, ?, ?, 'pending', 'web_upload')""",
                    (upload.filename, file_hash, file_size),
                )
                doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()

            created.append({
                "id": doc["id"],
                "original_filename": doc["original_filename"],
                "file_hash": doc["file_hash"],
                "status": doc["status"],
            })
            try:
                from backend.notifications.notifier import notify
                notify("ingested", {
                    "id": doc["id"],
                    "original_filename": doc["original_filename"],
                    "file_hash": doc["file_hash"],
                    "submission_channel": "web_upload",
                    "sender_identifier": None,
                })
            except Exception:
                pass

        finally:
            os.unlink(tmp_path)

    return {"documents": created, "duplicates": duplicates}
