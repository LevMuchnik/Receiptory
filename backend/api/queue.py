from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.processing.queue import get_queue_status

router = APIRouter()


@router.get("/queue/status")
def queue_status(username: str = Depends(require_auth)):
    return get_queue_status()
