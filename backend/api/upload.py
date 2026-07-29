import os
import tempfile
import logging
from fastapi import APIRouter, UploadFile, File, Depends, Request

from backend.auth import require_auth
from backend.database import get_connection
from backend.ingestion.service import ingest_local_file

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
            result = ingest_local_file(
                tmp_path,
                filename=upload.filename or "document.pdf",
                data_dir=data_dir,
                submission_channel="web_upload",
            )
            if result.status == "duplicate":
                duplicates.append({
                    "filename": upload.filename,
                    "file_hash": result.file_hash,
                    "existing_id": result.document_id,
                })
                continue

            with get_connection() as conn:
                doc = conn.execute(
                    "SELECT * FROM documents WHERE id = ?", (result.document_id,)
                ).fetchone()

            created.append({
                "id": doc["id"],
                "original_filename": doc["original_filename"],
                "file_hash": doc["file_hash"],
                "status": doc["status"],
            })

        finally:
            os.unlink(tmp_path)

    return {"documents": created, "duplicates": duplicates}
