import csv
import io
import os
import re
import zipfile
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.config import get_setting
from backend.database import get_connection
from backend.models import ExportRequest
from backend.storage import get_filed_path

logger = logging.getLogger(__name__)
router = APIRouter()


def _slugify(value: str) -> str:
    """Collapse whitespace/unsafe filename chars into single underscores."""
    value = re.sub(r"[^\w\-]+", "_", value.strip(), flags=re.UNICODE)
    return value.strip("_")


def _export_basename(body: ExportRequest, rows: list) -> str:
    """Build a descriptive export filename: <name>_receiptory_<dates>."""
    export_name = (get_setting("export_name") or "").strip()
    if not export_name:
        names = get_setting("business_names") or []
        export_name = names[0] if names else ""
    name_part = _slugify(export_name)

    date_part = ""
    if body.preset == "month" and body.month:
        try:
            date_part = datetime.strptime(body.month, "%Y-%m").strftime("%Y_%m")
        except ValueError:
            date_part = body.month
    elif body.preset == "full_year" and body.year is not None:
        date_part = f"{body.year:04d}"
    elif body.date_from or body.date_to:
        date_part = "_to_".join(d.replace("-", "_") for d in (body.date_from, body.date_to) if d)
    else:
        # Derive range from the exported rows, else fall back to today.
        date_col = "submission_date" if body.date_basis == "ingestion" else "receipt_date"
        dates = sorted(str(r[date_col])[:10].replace("-", "_") for r in rows if r[date_col])
        if dates and dates[0] == dates[-1]:
            date_part = dates[0]
        elif dates:
            date_part = f"{dates[0]}_to_{dates[-1]}"
        else:
            date_part = datetime.now(timezone.utc).strftime("%Y_%m_%d")

    parts = [p for p in (name_part, "receiptory", date_part) if p]
    # Final slugify sanitizes any unvalidated input (e.g. date_from on the
    # since_last_export path) before it reaches the Content-Disposition header.
    return _slugify("_".join(parts))

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

    # Build query conditions
    conditions = ["d.is_deleted = 0"]
    params: list = []

    # If specific document IDs provided, filter to those only
    if body.document_ids:
        placeholders = ",".join("?" * len(body.document_ids))
        conditions.append(f"d.id IN ({placeholders})")
        params.extend(body.document_ids)
    else:
        conditions.append("d.status = 'processed'")

    date_col = "d.submission_date" if body.date_basis == "ingestion" else "d.receipt_date"

    if not body.document_ids and body.preset == "since_last_export":
        conditions.append("d.last_exported_date IS NULL")
    elif body.preset == "month" and body.month:
        try:
            month_dt = datetime.strptime(body.month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")
        if month_dt.month == 12:
            next_month_dt = month_dt.replace(year=month_dt.year + 1, month=1)
        else:
            next_month_dt = month_dt.replace(month=month_dt.month + 1)
        conditions.append(f"{date_col} >= ?")
        conditions.append(f"{date_col} < ?")
        params.extend([month_dt.strftime("%Y-%m-01"), next_month_dt.strftime("%Y-%m-01")])
    elif body.preset == "full_year" and body.year is not None:
        conditions.append(f"{date_col} >= ?")
        conditions.append(f"{date_col} < ?")
        params.extend([f"{body.year:04d}-01-01", f"{body.year + 1:04d}-01-01"])
    else:
        if body.date_from:
            try:
                date_from_dt = datetime.strptime(body.date_from, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=422, detail="date_from must be in YYYY-MM-DD format")
            conditions.append(f"{date_col} >= ?")
            params.append(date_from_dt.strftime("%Y-%m-%d"))
        if body.date_to:
            try:
                dt = datetime.strptime(body.date_to, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=422, detail="date_to must be in YYYY-MM-DD format")
            if body.date_basis == "ingestion":
                # submission_date is a full ISO timestamp; use exclusive upper bound
                # to include all documents ingested up to end-of-day UTC on date_to
                next_day = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
                conditions.append(f"{date_col} < ?")
                params.append(next_day)
            else:
                conditions.append(f"{date_col} <= ?")
                params.append(dt.strftime("%Y-%m-%d"))

    if body.status:
        conditions.append("d.status = ?")
        params.append(body.status)
    if body.category_id:
        conditions.append("d.category_id = ?")
        params.append(body.category_id)
    if body.document_type:
        conditions.append("d.document_type = ?")
        params.append(body.document_type)
    if body.section:
        conditions.append("c.section = ?")
        params.append(body.section)

    where = " AND ".join(conditions)

    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT d.*, c.name as category_name, c.section as category_section
                FROM documents d
                LEFT JOIN categories c ON d.category_id = c.id
                WHERE {where}
                ORDER BY {date_col}""",
            params,
        ).fetchall()

    # Documents whose PDF did not make it into the zip. They must NOT be
    # stamped as exported below: preset="since_last_export" filters on
    # last_exported_date IS NULL, so marking an undelivered document would
    # retire it from every future incremental export, silently and forever.
    omitted: set[int] = set()

    # Build zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add PDFs organized by section/category
        for row in rows:
            # Slugified, because BOTH of these reach the zip arcname and neither
            # is validated on the way in: categories.name is a bare `str` in
            # CategoryCreate, and section is only a Literal on write, not on
            # read. zipfile.ZipInfo.from_file normpaths the arcname and strips a
            # LEADING separator but keeps ".." intact -- verified:
            # "expense/../../../tmp/evil/x.pdf" is stored as
            # "../../tmp/evil/x.pdf", which unzip happily writes outside the
            # extraction directory. Hardening only the filename would leave two
            # of three path components open, which is the exact mistake #45 cost
            # us on scanner_test_frames.frame_path.
            section = _slugify(row["category_section"] or "") or "other"
            # Prefixed with the category id, because _slugify is many-to-one:
            # "Travel/Meals", "Travel Meals" and "Travel_Meals" all collapse to
            # one folder, and a name made entirely of punctuation slugs to ""
            # and would land in the real "uncategorized" folder. Nothing is
            # lost either way (filenames carry a hash suffix) but documents
            # would be filed under the wrong heading in the archive someone
            # else reads. Ids start at 1, so 0 is a fallback no row can claim.
            cat_id = row["category_id"] or 0
            cat_name = f"{cat_id}-" + (_slugify(row["category_name"] or "") or "uncategorized")
            stored = row["stored_filename"]
            if not stored:
                # No filed copy was ever recorded, so there is no PDF to ship.
                # Reachable: when document_ids is supplied the query skips the
                # status='processed' filter, so a pending or failed row can be
                # exported by id straight from the Documents list. Stamping it
                # would retire it from every future since_last_export run --
                # the same defect as the two branches below, one case wider.
                omitted.add(row["id"])
                continue
            if stored:
                # Resolved, not joined. An absolute stored_filename made
                # os.path.join return that path verbatim, os.path.exists say
                # yes, and this line copy the file into the zip as root -- with
                # an arcname of "<section>/<cat>//etc/shadow". One bad row must
                # not take the export down either, so it is skipped and logged.
                try:
                    pdf_path = get_filed_path(stored, data_dir)
                except ValueError:
                    omitted.add(row["id"])
                    logger.warning(
                        "Document %s has a stored_filename outside filed/; its "
                        "PDF is omitted from the export (the metadata row is "
                        "still included)", row["id"]
                    )
                    continue
                if os.path.exists(pdf_path):
                    try:
                        zf.write(pdf_path, f"{section}/{cat_name}/{stored}")
                    except FileNotFoundError:
                        # exists() then write() is a gap, and this change is what
                        # made it reachable: storage.remove_filed now genuinely
                        # unlinks this exact path when a reprocess renames a
                        # document. Export takes no lock, so a reprocess mid-zip
                        # would otherwise raise out of here as an unhandled 500
                        # and lose the whole archive -- and the Documents page
                        # offers batch-reprocess and export on the same
                        # selection. One document omitted beats no export.
                        omitted.add(row["id"])
                        logger.warning(
                            "Document %s: %s vanished while the export was being "
                            "built (concurrent reprocess); its PDF is omitted",
                            row["id"], stored
                        )
                else:
                    # Pre-existing trigger, same consequence: the row is in the
                    # metadata but its PDF is not in the archive.
                    omitted.add(row["id"])
                    logger.warning(
                        "Document %s: filed copy %s is missing; its PDF is "
                        "omitted from the export", row["id"], stored
                    )

        # Add CSV metadata
        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=EXPORT_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row[f] for f in EXPORT_CSV_FIELDS if f in row.keys()})
        zf.writestr("metadata.csv", csv_buf.getvalue())

        # Add Excel metadata
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Documents"
            ws.append(EXPORT_CSV_FIELDS)
            for row in rows:
                ws.append([row[f] if f in row.keys() else None for f in EXPORT_CSV_FIELDS])
            # Auto-size columns
            for col in ws.columns:
                max_len = max((len(str(cell.value or "")) for cell in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)
            xlsx_buf = io.BytesIO()
            wb.save(xlsx_buf)
            zf.writestr("metadata.xlsx", xlsx_buf.getvalue())
        except Exception as e:
            logger.warning(f"Excel export failed, skipping: {e}")

    # Update last_exported_date
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_ids = [row["id"] for row in rows if row["id"] not in omitted]
    if omitted:
        logger.warning(
            "%d document(s) had no PDF in this export and were left unstamped, "
            "so they stay eligible for the next since_last_export run",
            len(omitted),
        )
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        with get_connection() as conn:
            conn.execute(
                f"UPDATE documents SET last_exported_date = ? WHERE id IN ({placeholders})",
                [now] + doc_ids,
            )

    buf.seek(0)
    filename = f"{_export_basename(body, rows)}.zip"
    # ASCII fallback for legacy clients; RFC 5987 for unicode (e.g. Hebrew names).
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "receiptory_export.zip"
    disposition = (
        f"attachment; filename=\"{ascii_name}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )
