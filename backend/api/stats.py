from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.database import get_connection

router = APIRouter()


@router.get("/stats/dashboard")
def dashboard_stats(
    date_from: str | None = None,
    date_to: str | None = None,
    username: str = Depends(require_auth),
):
    with get_connection() as conn:
        processed = conn.execute(
            """SELECT COUNT(*) as c FROM documents
               WHERE status = 'processed'
               AND processing_date >= strftime('%Y-%m-01T00:00:00Z', 'now')"""
        ).fetchone()["c"]

        pending_review = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE status = 'needs_review' AND is_deleted = 0"
        ).fetchone()["c"]

        # Expenses only: exclude issued documents (section != 'issued')
        expense_conditions = [
            "d.status = 'processed'",
            "d.is_deleted = 0",
            "d.total_amount IS NOT NULL",
            "(c.section IS NULL OR c.section != 'issued')",
            "d.document_type != 'issued_invoice'",
        ]
        params: list = []
        if date_from:
            expense_conditions.append("d.receipt_date >= ?")
            params.append(date_from)
        if date_to:
            expense_conditions.append("d.receipt_date <= ?")
            params.append(date_to)

        where = " AND ".join(expense_conditions)
        expenses = conn.execute(
            f"""SELECT c.name, SUM(d.total_amount) as total
               FROM documents d
               LEFT JOIN categories c ON d.category_id = c.id
               WHERE {where}
               GROUP BY c.name
               ORDER BY total DESC""",
            params,
        ).fetchall()

        recent = conn.execute(
            """SELECT id, original_filename, status, submission_channel, submission_date
               FROM documents
               ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()

    return {
        "processed_this_month": processed,
        "pending_review_count": pending_review,
        "total_expenses_by_category": [{"category": r["name"], "total": r["total"]} for r in expenses],
        "recent_activity": [dict(r) for r in recent],
    }


@router.get("/stats/processing-costs")
def processing_costs(username: str = Depends(require_auth)):
    with get_connection() as conn:
        totals = conn.execute(
            """SELECT
                COALESCE(SUM(processing_tokens_in), 0) as total_in,
                COALESCE(SUM(processing_tokens_out), 0) as total_out,
                COALESCE(SUM(processing_cost_usd), 0) as total_cost
               FROM documents WHERE processing_model IS NOT NULL"""
        ).fetchone()

        by_model = conn.execute(
            """SELECT processing_model,
                COUNT(*) as doc_count,
                SUM(processing_tokens_in) as tokens_in,
                SUM(processing_tokens_out) as tokens_out,
                SUM(processing_cost_usd) as cost
               FROM documents
               WHERE processing_model IS NOT NULL
               GROUP BY processing_model"""
        ).fetchall()

    return {
        "total_tokens_in": totals["total_in"],
        "total_tokens_out": totals["total_out"],
        "total_cost_usd": totals["total_cost"],
        "by_model": [dict(r) for r in by_model],
    }
