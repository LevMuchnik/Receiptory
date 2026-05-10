import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.auth import require_auth
from backend.config import get_setting, set_setting
from backend.database import get_connection
from backend.storage import get_scanner_test_frame_path, save_scanner_test_frame

logger = logging.getLogger(__name__)

router = APIRouter()


class TestFramePatch(BaseModel):
    ground_truth_json: Optional[str] = None
    notes: Optional[str] = None


class ActiveConfig(BaseModel):
    detector: str
    params: dict[str, Any] = {}


@router.post("/scanner/test-frames")
async def create_test_frame(
    request: Request,
    file: UploadFile = File(...),
    width: int = Form(...),
    height: int = Form(...),
    detector_name: str | None = Form(None),
    corners_at_capture_json: str | None = Form(None),
    notes: str | None = Form(None),
    username: str = Depends(require_auth),
):
    data_dir = request.app.state.data_dir
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty frame")

    rel_path = save_scanner_test_frame(content, data_dir)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO scanner_test_frames
                   (frame_path, width, height, detector_name, corners_at_capture_json, notes,
                    captured_at)
               VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))""",
            (rel_path, width, height, detector_name, corners_at_capture_json, notes),
        )
        frame_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {"id": frame_id, "frame_path": rel_path}


@router.get("/scanner/test-frames")
def list_test_frames(username: str = Depends(require_auth)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, frame_path, captured_at, width, height, detector_name,
                      corners_at_capture_json, ground_truth_json, notes
               FROM scanner_test_frames
               ORDER BY captured_at DESC, id DESC"""
        ).fetchall()
    return {"frames": [dict(r) for r in rows]}


@router.get("/scanner/test-frames/{frame_id}/image")
def get_test_frame_image(
    frame_id: int,
    request: Request,
    username: str = Depends(require_auth),
):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        row = conn.execute(
            "SELECT frame_path FROM scanner_test_frames WHERE id = ?", (frame_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Frame not found")
    abs_path = get_scanner_test_frame_path(row["frame_path"], data_dir)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Frame file missing")
    return FileResponse(abs_path, media_type="image/jpeg")


@router.patch("/scanner/test-frames/{frame_id}")
def patch_test_frame(
    frame_id: int,
    body: TestFramePatch,
    username: str = Depends(require_auth),
):
    fields: list[str] = []
    values: list[Any] = []
    if body.ground_truth_json is not None:
        fields.append("ground_truth_json = ?")
        values.append(body.ground_truth_json)
    if body.notes is not None:
        fields.append("notes = ?")
        values.append(body.notes)
    if not fields:
        return {"updated": 0}
    values.append(frame_id)
    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE scanner_test_frames SET {', '.join(fields)} WHERE id = ?",
            tuple(values),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Frame not found")
    return {"updated": 1}


@router.delete("/scanner/test-frames/{frame_id}")
def delete_test_frame(
    frame_id: int,
    request: Request,
    username: str = Depends(require_auth),
):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        row = conn.execute(
            "SELECT frame_path FROM scanner_test_frames WHERE id = ?", (frame_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Frame not found")
        conn.execute("DELETE FROM scanner_test_frames WHERE id = ?", (frame_id,))
    abs_path = get_scanner_test_frame_path(row["frame_path"], data_dir)
    if os.path.exists(abs_path):
        try:
            os.unlink(abs_path)
        except OSError as e:
            logger.warning(f"Failed to delete frame file {abs_path}: {e}")
    return {"deleted": frame_id}


@router.get("/scanner/active-config")
def get_active_config(username: str = Depends(require_auth)):
    cfg = get_setting("scanner_active_config")
    if not isinstance(cfg, dict):
        cfg = {"detector": "classical", "params": {}}
    return cfg


@router.put("/scanner/active-config")
def put_active_config(
    body: ActiveConfig,
    username: str = Depends(require_auth),
):
    set_setting("scanner_active_config", body.model_dump())
    return {"message": "Active config updated"}
