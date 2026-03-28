import asyncio
from fastapi import APIRouter, Depends, Request, HTTPException

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
    # For local backups this could serve the file; for remote, redirect to rclone
    raise HTTPException(status_code=501, detail="Download from remote not yet implemented")


@router.delete("/backup/{backup_id}")
def delete_backup(backup_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
    return {"message": "Backup record deleted"}
