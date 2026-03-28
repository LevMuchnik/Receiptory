import asyncio
import io
import os
import zipfile
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.database import get_connection
from backend.backup.scheduler import run_backup

router = APIRouter()


@router.post("/backup/trigger")
async def trigger_backup(request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    backup_id = await run_backup(data_dir, trigger="manual")
    return {"message": "Backup triggered", "backup_id": backup_id}


@router.get("/backup/history")
def backup_history(username: str = Depends(require_auth)):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM backups ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/backup/{backup_id}/download")
def download_backup(backup_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")

    local_path = row["local_path"]
    if not local_path or not os.path.exists(local_path):
        raise HTTPException(status_code=404, detail="Backup files not available locally (may have been uploaded to remote storage)")

    # Zip the backup directory and stream it
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(local_path):
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                arcname = os.path.relpath(full_path, local_path)
                zf.write(full_path, arcname)
    buf.seek(0)

    backup_name = os.path.basename(local_path)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={backup_name}.zip"},
    )


@router.delete("/backup/{backup_id}")
def delete_backup(backup_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
    return {"message": "Backup record deleted"}
