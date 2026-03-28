import csv
import io
import os
import zipfile
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.database import get_connection
from backend.models import ExportRequest

logger = logging.getLogger(__name__)
router = APIRouter()

EXPORT_CSV_FIELDS = [
    "id", "document_type", "original_filename", "stored_filename", "receipt_date",
    "vendor_name", "vendor_tax_id", "vendor_receipt_id", "client_name", "client_tax_id",
    "description", "subtotal", "tax_amount", "total_amount", "currency",
    "payment_method", "payment_identifier", "language", "status",
    "submission_date", "submission_channel", "category_name",
]


@router.post("/export")
def export_documents(body: ExportRequest, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    filed_dir = os.path.join(data_dir, "storage", "filed")

    # Build query conditions
    conditions = ["d.is_deleted = 0", "d.status = 'processed'"]
    params: list = []

    if body.preset == "since_last_export":
        conditions.append("d.last_exported_date IS NULL")
    elif body.preset == "month" and body.month:
        conditions.append("d.receipt_date >= ?")
        conditions.append("d.receipt_date < ?")
        year, month = body.month.split("-")
        next_month = int(month) + 1
        next_year = int(year)
        if next_month > 12:
            next_month = 1
            next_year += 1
        params.extend([f"{year}-{month}-01", f"{next_year:04d}-{next_month:02d}-01"])
    elif body.preset == "full_year" and body.year:
        conditions.append("d.receipt_date >= ?")
        conditions.append("d.receipt_date < ?")
        params.extend([f"{body.year}-01-01", f"{body.year + 1}-01-01"])
    else:
        if body.date_from:
            conditions.append("d.receipt_date >= ?")
            params.append(body.date_from)
        if body.date_to:
            conditions.append("d.receipt_date <= ?")
            params.append(body.date_to)

    if body.status:
        conditions.append("d.status = ?")
        params.append(body.status)
    if body.category_id:
        conditions.append("d.category_id = ?")
        params.append(body.category_id)
    if body.document_type:
        conditions.append("d.document_type = ?")
        params.append(body.document_type)

    where = " AND ".join(conditions)

    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT d.*, c.name as category_name
                FROM documents d
                LEFT JOIN categories c ON d.category_id = c.id
                WHERE {where}
                ORDER BY d.receipt_date""",
            params,
        ).fetchall()

    # Build zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add PDFs organized by category
        for row in rows:
            cat_name = row["category_name"] or "uncategorized"
            stored = row["stored_filename"]
            if stored:
                pdf_path = os.path.join(filed_dir, stored)
                if os.path.exists(pdf_path):
                    zf.write(pdf_path, f"{cat_name}/{stored}")

        # Add CSV metadata
        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=EXPORT_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row[f] for f in EXPORT_CSV_FIELDS if f in row.keys()})
        zf.writestr("metadata.csv", csv_buf.getvalue())

    # Update last_exported_date
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_ids = [row["id"] for row in rows]
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        with get_connection() as conn:
            conn.execute(
                f"UPDATE documents SET last_exported_date = ? WHERE id IN ({placeholders})",
                [now] + doc_ids,
            )

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=receiptory_export.zip"},
    )
