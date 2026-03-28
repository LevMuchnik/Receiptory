import json
import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from backend.auth import require_auth
from backend.database import get_connection
from backend.storage import render_page, clear_page_cache, get_file_path
from backend.config import get_setting
from backend.models import (
    DocumentResponse,
    DocumentUpdate,
    DocumentListResponse,
    BatchReprocessRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _row_to_response(row) -> dict:
    """Convert a sqlite3.Row to a DocumentResponse-compatible dict."""
    d = dict(row)
    # Parse JSON fields
    for field in ("line_items", "additional_fields", "edit_history"):
        if d.get(field) and isinstance(d[field], str):
            d[field] = json.loads(d[field])
    # Bool conversions
    d["manually_edited"] = bool(d.get("manually_edited", 0))
    d["is_deleted"] = bool(d.get("is_deleted", 0))
    return d


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    status: str | None = None,
    category_id: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    channel: str | None = None,
    search: str | None = None,
    missing_info: bool = False,
    sort_by: str = "submission_date",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    username: str = Depends(require_auth),
):
    conditions = ["d.is_deleted = 0"]
    params: list = []

    if status:
        statuses = [s.strip() for s in status.split(",")]
        placeholders = ",".join("?" * len(statuses))
        conditions.append(f"d.status IN ({placeholders})")
        params.extend(statuses)
    if category_id:
        cat_ids = [int(c.strip()) for c in str(category_id).split(",")]
        placeholders = ",".join("?" * len(cat_ids))
        conditions.append(f"d.category_id IN ({placeholders})")
        params.extend(cat_ids)
    if document_type:
        types = [t.strip() for t in document_type.split(",")]
        placeholders = ",".join("?" * len(types))
        conditions.append(f"d.document_type IN ({placeholders})")
        params.extend(types)
    if date_from:
        conditions.append("d.receipt_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("d.receipt_date <= ?")
        params.append(date_to)
    if channel:
        conditions.append("d.submission_channel = ?")
        params.append(channel)
    if search:
        conditions.append("d.id IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?)")
        params.append(search)
    if missing_info:
        conditions.append("(d.receipt_date IS NULL OR d.vendor_receipt_id IS NULL)")

    where = " AND ".join(conditions)
    allowed_sort = {"submission_date", "receipt_date", "vendor_name", "total_amount", "status", "created_at"}
    if sort_by not in allowed_sort:
        sort_by = "submission_date"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) as c FROM documents d WHERE {where}", params
        ).fetchone()["c"]

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""SELECT d.*, c.name as category_name
                FROM documents d
                LEFT JOIN categories c ON d.category_id = c.id
                WHERE {where}
                ORDER BY d.{sort_by} {sort_order}
                LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        ).fetchall()

    return DocumentListResponse(
        documents=[_row_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/duplicates")
def list_duplicates(username: str = Depends(require_auth)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT receipt_date, vendor_receipt_id, GROUP_CONCAT(id) as ids
               FROM documents
               WHERE is_deleted = 0
                 AND receipt_date IS NOT NULL
                 AND vendor_receipt_id IS NOT NULL
               GROUP BY receipt_date, vendor_receipt_id
               HAVING COUNT(*) > 1"""
        ).fetchall()

    groups = []
    for row in rows:
        ids = [int(x) for x in row["ids"].split(",")]
        with get_connection() as conn:
            docs = conn.execute(
                f"SELECT d.*, c.name as category_name FROM documents d LEFT JOIN categories c ON d.category_id = c.id WHERE d.id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        groups.append({
            "receipt_date": row["receipt_date"],
            "vendor_receipt_id": row["vendor_receipt_id"],
            "documents": [_row_to_response(d) for d in docs],
        })
    return groups


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT d.*, c.name as category_name FROM documents d LEFT JOIN categories c ON d.category_id = c.id WHERE d.id = ?",
            (doc_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return _row_to_response(row)


@router.patch("/documents/{doc_id}")
def edit_document(doc_id: int, update: DocumentUpdate, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    changes = update.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build edit history entries
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = json.loads(existing["edit_history"] or "[]")
    for field, new_val in changes.items():
        old_val = existing[field]
        if str(old_val) != str(new_val):
            history.append({
                "field": field,
                "old_value": str(old_val) if old_val is not None else None,
                "new_value": str(new_val) if new_val is not None else None,
                "timestamp": now,
            })

    set_clauses = [f"{k} = ?" for k in changes]
    set_clauses.extend(["manually_edited = 1", "edit_history = ?", "updated_at = ?"])
    values = list(changes.values()) + [json.dumps(history), now, doc_id]

    with get_connection() as conn:
        conn.execute(
            f"UPDATE documents SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        row = conn.execute(
            "SELECT d.*, c.name as category_name FROM documents d LEFT JOIN categories c ON d.category_id = c.id WHERE d.id = ?",
            (doc_id,),
        ).fetchone()

    return _row_to_response(row)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET is_deleted = 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (doc_id,),
        )
    return {"message": "Document deleted"}


@router.post("/documents/{doc_id}/reprocess")
def reprocess_document(doc_id: int, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        conn.execute(
            """UPDATE documents SET
                status = 'pending', processing_error = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ?""",
            (doc_id,),
        )
    cache_dir = os.path.join(data_dir, "storage", "page_cache")
    clear_page_cache(cache_dir, doc_id)
    return {"message": "Document queued for reprocessing"}


@router.post("/documents/batch-reprocess")
def batch_reprocess(body: BatchReprocessRequest, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    cache_dir = os.path.join(data_dir, "storage", "page_cache")

    if body.document_ids:
        placeholders = ",".join("?" * len(body.document_ids))
        with get_connection() as conn:
            conn.execute(
                f"UPDATE documents SET status = 'pending', processing_error = NULL WHERE id IN ({placeholders})",
                body.document_ids,
            )
        for did in body.document_ids:
            clear_page_cache(cache_dir, did)
        return {"message": f"Queued {len(body.document_ids)} documents for reprocessing"}

    conditions = ["is_deleted = 0"]
    params: list = []
    if body.status:
        conditions.append("status = ?")
        params.append(body.status)
    if body.category_id:
        conditions.append("category_id = ?")
        params.append(body.category_id)

    where = " AND ".join(conditions)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE documents SET status = 'pending', processing_error = NULL WHERE {where}",
            params,
        )
        count = conn.execute("SELECT changes()").fetchone()[0]
    return {"message": f"Queued {count} documents for reprocessing"}


@router.get("/documents/{doc_id}/file/{file_type}")
def serve_file(doc_id: int, file_type: str, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"
    try:
        path = get_file_path(file_type, doc["file_hash"], ext, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid file type: {file_type}")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path)


@router.get("/documents/{doc_id}/pages/{page_num}")
def serve_page(doc_id: int, page_num: int, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"
    # Try converted first, fall back to original
    converted_path = os.path.join(data_dir, "storage", "converted", f"{doc['file_hash']}.pdf")
    if os.path.exists(converted_path):
        pdf_path = converted_path
    else:
        pdf_path = get_file_path("original", doc["file_hash"], ext, data_dir)

    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF not found")

    dpi = get_setting("page_render_dpi")
    cache_dir = os.path.join(data_dir, "storage", "page_cache")
    try:
        png_bytes = render_page(pdf_path, page_num, dpi=dpi, cache_dir=cache_dir, doc_id=doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return Response(content=png_bytes, media_type="image/png")
