import os
from fastapi import APIRouter, Depends, Request, Query

from backend.auth import require_auth

router = APIRouter()


@router.get("/logs")
def get_logs(
    request: Request,
    limit: int = Query(100, le=1000),
    level: str | None = None,
    username: str = Depends(require_auth),
):
    data_dir = request.app.state.data_dir
    log_path = os.path.join(data_dir, "logs", "receiptory.log")

    if not os.path.exists(log_path):
        return {"lines": []}

    with open(log_path, "r") as f:
        lines = f.readlines()

    # Filter by level if specified
    if level:
        level_upper = level.upper()
        lines = [l for l in lines if level_upper in l]

    # Return last N lines
    return {"lines": lines[-limit:]}
