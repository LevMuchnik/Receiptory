from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import require_auth
from backend.database import get_connection
from backend.models import CategoryCreate, CategoryUpdate, CategoryResponse, ReorderRequest

router = APIRouter()


@router.get("/categories")
def list_categories(
    include_deleted: bool = False,
    username: str = Depends(require_auth),
):
    with get_connection() as conn:
        deleted_filter = "" if include_deleted else "WHERE c.is_deleted = 0"
        rows = conn.execute(
            f"""SELECT c.*, COALESCE(cnt.doc_count, 0) AS document_count
                FROM categories c
                LEFT JOIN (
                    SELECT category_id, COUNT(*) AS doc_count
                    FROM documents WHERE is_deleted = 0
                    GROUP BY category_id
                ) cnt ON cnt.category_id = c.id
                {deleted_filter}
                ORDER BY c.is_system ASC, c.display_order ASC, c.name ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.patch("/categories/reorder")
def reorder_categories(body: ReorderRequest, username: str = Depends(require_auth)):
    with get_connection() as conn:
        for item in body.order:
            cat = conn.execute("SELECT is_system FROM categories WHERE id = ?", (item.id,)).fetchone()
            if not cat:
                raise HTTPException(status_code=404, detail=f"Category {item.id} not found")
            if cat["is_system"]:
                raise HTTPException(status_code=400, detail="Cannot reorder system categories")
            conn.execute(
                "UPDATE categories SET display_order = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (item.display_order, item.id),
            )
    return {"message": "Order updated"}


@router.post("/categories")
def create_category(body: CategoryCreate, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (body.name,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Category name already exists")

        conn.execute(
            """INSERT INTO categories (name, description)
               VALUES (?, ?)""",
            (body.name, body.description),
        )
        cat_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    return dict(row)


@router.patch("/categories/{cat_id}")
def update_category(cat_id: int, body: CategoryUpdate, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Category not found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses = [f"{k} = ?" for k in updates]
        set_clauses.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        values = list(updates.values()) + [cat_id]

        conn.execute(
            f"UPDATE categories SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    return dict(row)


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Category not found")
        if existing["is_system"]:
            raise HTTPException(status_code=400, detail="Cannot delete system categories")

        conn.execute(
            "UPDATE categories SET is_deleted = 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (cat_id,),
        )
    return {"message": "Category deleted"}
