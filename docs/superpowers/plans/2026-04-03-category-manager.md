# Category Manager Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the card-based category manager with an editable table supporting drag-and-drop reordering, inline editing, and document counts.

**Architecture:** Backend gets a new `/categories/reorder` endpoint and document count in the list response. Frontend CategoryManager is rewritten as a sortable table using `@dnd-kit/sortable`. Inline editing via click-to-edit cells, reordering via drag handles and arrow buttons.

**Tech Stack:** `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities` (frontend), FastAPI (backend)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `backend/api/categories.py` | Add reorder endpoint, add document_count to list |
| Modify | `backend/models.py` | Add ReorderRequest model |
| Modify | `tests/test_categories_api.py` | Tests for reorder endpoint and document_count |
| Rewrite | `frontend/src/components/CategoryManager.tsx` | Sortable table with inline edit |
| Modify | `frontend/package.json` | Add @dnd-kit dependencies |

---

### Task 1: Backend — Add document_count to category list and reorder endpoint

**Files:**
- Modify: `backend/models.py:22-31`
- Modify: `backend/api/categories.py:10-24` and add new endpoint
- Modify: `tests/test_categories_api.py`

- [ ] **Step 1: Write failing tests for document_count and reorder**

Add to `tests/test_categories_api.py`:

```python
def test_list_categories_includes_document_count(authed_client):
    resp = authed_client.get("/api/categories")
    assert resp.status_code == 200
    cats = resp.json()
    # Every category should have a document_count field
    for c in cats:
        assert "document_count" in c
        assert isinstance(c["document_count"], int)


def test_reorder_categories(authed_client):
    # Create two categories
    r1 = authed_client.post("/api/categories", json={"name": "zzz_first", "description": "first"})
    r2 = authed_client.post("/api/categories", json={"name": "aaa_second", "description": "second"})
    id1 = r1.json()["id"]
    id2 = r2.json()["id"]

    # Reorder: put second before first
    resp = authed_client.patch("/api/categories/reorder", json={
        "order": [
            {"id": id2, "display_order": 0},
            {"id": id1, "display_order": 1},
        ]
    })
    assert resp.status_code == 200

    # Verify order
    cats = authed_client.get("/api/categories").json()
    user_cats = [c for c in cats if not c["is_system"]]
    ids_in_order = [c["id"] for c in user_cats]
    assert ids_in_order.index(id2) < ids_in_order.index(id1)


def test_reorder_rejects_system_categories(authed_client):
    resp = authed_client.get("/api/categories")
    system_cat = [c for c in resp.json() if c["is_system"]][0]

    resp = authed_client.patch("/api/categories/reorder", json={
        "order": [{"id": system_cat["id"], "display_order": 0}]
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_categories_api.py -v -k "document_count or reorder"`
Expected: FAIL

- [ ] **Step 3: Add ReorderRequest model**

In `backend/models.py`, add after the `CategoryUpdate` class (line 31):

```python
class ReorderItem(BaseModel):
    id: int
    display_order: int


class ReorderRequest(BaseModel):
    order: list[ReorderItem]
```

- [ ] **Step 4: Update list_categories to include document_count**

In `backend/api/categories.py`, replace the `list_categories` function (lines 10-24):

```python
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
```

- [ ] **Step 5: Add reorder endpoint**

In `backend/api/categories.py`, add the import and new endpoint after the existing imports:

Add `ReorderRequest` to the import from models:
```python
from backend.models import CategoryCreate, CategoryUpdate, CategoryResponse, ReorderRequest
```

Add the endpoint after `list_categories`:

```python
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
```

- [ ] **Step 6: Fix existing test for new category names**

The seeded categories changed from `office_supplies` to `Office & Supplies`. Update `test_list_categories` in `tests/test_categories_api.py`:

```python
def test_list_categories(authed_client):
    resp = authed_client.get("/api/categories")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert "Office & Supplies" in names
    assert "pending" in names
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_categories_api.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/api/categories.py backend/models.py tests/test_categories_api.py
git commit -m "feat: add category reorder endpoint and document_count to list"
```

---

### Task 2: Frontend — Install dnd-kit dependencies

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install dnd-kit packages**

Run: `cd frontend && npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities`

- [ ] **Step 2: Verify installation**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore: add @dnd-kit dependencies for sortable category table"
```

---

### Task 3: Frontend — Rewrite CategoryManager as sortable table

**Files:**
- Rewrite: `frontend/src/components/CategoryManager.tsx`

- [ ] **Step 1: Rewrite CategoryManager.tsx**

Replace the entire contents of `frontend/src/components/CategoryManager.tsx` with:

```tsx
import { useCallback, useEffect, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Category {
  id: number;
  name: string;
  description: string | null;
  is_system: boolean;
  display_order: number | null;
  document_count: number;
}

interface EditingCell {
  id: number;
  field: "name" | "description";
}

function SortableRow({
  category,
  index,
  total,
  editingCell,
  editValue,
  onStartEdit,
  onEditChange,
  onSaveEdit,
  onCancelEdit,
  onMoveUp,
  onMoveDown,
  onDelete,
}: {
  category: Category;
  index: number;
  total: number;
  editingCell: EditingCell | null;
  editValue: string;
  onStartEdit: (id: number, field: "name" | "description", value: string) => void;
  onEditChange: (value: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onMoveUp: (id: number) => void;
  onMoveDown: (id: number) => void;
  onDelete: (id: number, name: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: category.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const isEditingName = editingCell?.id === category.id && editingCell.field === "name";
  const isEditingDesc = editingCell?.id === category.id && editingCell.field === "description";

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") onSaveEdit();
    if (e.key === "Escape") onCancelEdit();
  };

  return (
    <tr ref={setNodeRef} style={style} className="border-b border-border hover:bg-accent/50 transition-colors">
      <td className="w-10 px-2 py-2 text-center">
        <button {...attributes} {...listeners} className="cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground">
          <span className="material-symbols-outlined text-lg">drag_indicator</span>
        </button>
      </td>
      <td className="px-3 py-2 w-52">
        {isEditingName ? (
          <Input
            autoFocus
            value={editValue}
            onChange={(e) => onEditChange(e.target.value)}
            onBlur={onSaveEdit}
            onKeyDown={handleKeyDown}
            className="h-8 text-sm"
          />
        ) : (
          <button
            className="text-left font-medium text-sm w-full hover:text-primary transition-colors"
            onClick={() => onStartEdit(category.id, "name", category.name)}
          >
            {category.name}
          </button>
        )}
      </td>
      <td className="px-3 py-2">
        {isEditingDesc ? (
          <Input
            autoFocus
            value={editValue}
            onChange={(e) => onEditChange(e.target.value)}
            onBlur={onSaveEdit}
            onKeyDown={handleKeyDown}
            className="h-8 text-sm"
          />
        ) : (
          <button
            className="text-left text-sm text-muted-foreground w-full hover:text-foreground transition-colors"
            onClick={() => onStartEdit(category.id, "description", category.description || "")}
          >
            {category.description || "—"}
          </button>
        )}
      </td>
      <td className="px-3 py-2 w-16 text-center text-sm text-muted-foreground">
        {category.document_count}
      </td>
      <td className="px-2 py-2 w-28">
        <div className="flex items-center gap-0.5">
          <button
            disabled={index === 0}
            onClick={() => onMoveUp(category.id)}
            className="p-1 rounded hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <span className="material-symbols-outlined text-base">arrow_upward</span>
          </button>
          <button
            disabled={index === total - 1}
            onClick={() => onMoveDown(category.id)}
            className="p-1 rounded hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <span className="material-symbols-outlined text-base">arrow_downward</span>
          </button>
          <button
            onClick={() => onDelete(category.id, category.name)}
            className="p-1 rounded hover:bg-destructive/10 text-destructive/70 hover:text-destructive ml-1"
          >
            <span className="material-symbols-outlined text-base">delete</span>
          </button>
        </div>
      </td>
    </tr>
  );
}

export default function CategoryManager() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [editingCell, setEditingCell] = useState<EditingCell | null>(null);
  const [editValue, setEditValue] = useState("");
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const load = useCallback(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  useEffect(() => { load(); }, [load]);

  const userCats = categories.filter((c) => !c.is_system);
  const systemCats = categories.filter((c) => c.is_system);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const persistOrder = async (reordered: Category[]) => {
    const order = reordered.map((c, i) => ({ id: c.id, display_order: i }));
    await api.patch("/categories/reorder", { order });
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = userCats.findIndex((c) => c.id === active.id);
    const newIndex = userCats.findIndex((c) => c.id === over.id);
    const reordered = arrayMove(userCats, oldIndex, newIndex);
    setCategories([...reordered, ...systemCats]);
    await persistOrder(reordered);
  };

  const handleMoveUp = async (id: number) => {
    const idx = userCats.findIndex((c) => c.id === id);
    if (idx <= 0) return;
    const reordered = arrayMove(userCats, idx, idx - 1);
    setCategories([...reordered, ...systemCats]);
    await persistOrder(reordered);
  };

  const handleMoveDown = async (id: number) => {
    const idx = userCats.findIndex((c) => c.id === id);
    if (idx < 0 || idx >= userCats.length - 1) return;
    const reordered = arrayMove(userCats, idx, idx + 1);
    setCategories([...reordered, ...systemCats]);
    await persistOrder(reordered);
  };

  const handleStartEdit = (id: number, field: "name" | "description", value: string) => {
    if (editingCell) handleSaveEdit();
    setEditingCell({ id, field });
    setEditValue(value);
  };

  const handleSaveEdit = async () => {
    if (!editingCell) return;
    const cat = categories.find((c) => c.id === editingCell.id);
    if (!cat) return;

    const currentValue = editingCell.field === "name" ? cat.name : (cat.description || "");
    if (editValue === currentValue || (editingCell.field === "name" && !editValue.trim())) {
      setEditingCell(null);
      return;
    }

    try {
      await api.patch(`/categories/${editingCell.id}`, { [editingCell.field]: editValue });
      load();
    } catch {
      // Revert on error
    }
    setEditingCell(null);
  };

  const handleCancelEdit = () => {
    setEditingCell(null);
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete "${name}"?`)) return;
    await api.delete(`/categories/${id}`);
    load();
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await api.post("/categories", { name: newName, description: newDesc || null });
    setNewName("");
    setNewDesc("");
    setAdding(false);
    load();
  };

  const handleCreateKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleCreate();
    if (e.key === "Escape") { setAdding(false); setNewName(""); setNewDesc(""); }
  };

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-border text-left">
              <th className="w-10 px-2 py-2"></th>
              <th className="px-3 py-2 font-semibold text-muted-foreground w-52">Name</th>
              <th className="px-3 py-2 font-semibold text-muted-foreground">Description</th>
              <th className="px-3 py-2 font-semibold text-muted-foreground w-16 text-center">Docs</th>
              <th className="px-2 py-2 font-semibold text-muted-foreground w-28">Actions</th>
            </tr>
          </thead>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={userCats.map((c) => c.id)} strategy={verticalListSortingStrategy}>
              <tbody>
                {userCats.map((cat, i) => (
                  <SortableRow
                    key={cat.id}
                    category={cat}
                    index={i}
                    total={userCats.length}
                    editingCell={editingCell}
                    editValue={editValue}
                    onStartEdit={handleStartEdit}
                    onEditChange={setEditValue}
                    onSaveEdit={handleSaveEdit}
                    onCancelEdit={handleCancelEdit}
                    onMoveUp={handleMoveUp}
                    onMoveDown={handleMoveDown}
                    onDelete={handleDelete}
                  />
                ))}
                {adding && (
                  <tr className="border-b border-border bg-accent/30">
                    <td className="w-10 px-2 py-2"></td>
                    <td className="px-3 py-2">
                      <Input
                        autoFocus
                        placeholder="Category name"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        onKeyDown={handleCreateKeyDown}
                        className="h-8 text-sm"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <Input
                        placeholder="Description"
                        value={newDesc}
                        onChange={(e) => setNewDesc(e.target.value)}
                        onKeyDown={handleCreateKeyDown}
                        onBlur={handleCreate}
                        className="h-8 text-sm"
                      />
                    </td>
                    <td className="px-3 py-2"></td>
                    <td className="px-2 py-2">
                      <button
                        onClick={() => { setAdding(false); setNewName(""); setNewDesc(""); }}
                        className="p-1 rounded hover:bg-accent text-muted-foreground"
                      >
                        <span className="material-symbols-outlined text-base">close</span>
                      </button>
                    </td>
                  </tr>
                )}
              </tbody>
            </SortableContext>
          </DndContext>

          {/* System categories */}
          {systemCats.length > 0 && (
            <tbody>
              <tr>
                <td colSpan={5} className="px-3 pt-4 pb-2 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                  System
                </td>
              </tr>
              {systemCats.map((cat) => (
                <tr key={cat.id} className="border-b border-border opacity-50">
                  <td className="w-10 px-2 py-2"></td>
                  <td className="px-3 py-2 font-medium">{cat.name}</td>
                  <td className="px-3 py-2 text-muted-foreground">{cat.description || "—"}</td>
                  <td className="px-3 py-2 text-center text-muted-foreground">{cat.document_count}</td>
                  <td className="px-2 py-2"></td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </div>

      {!adding && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => setAdding(true)}
        >
          <span className="material-symbols-outlined text-sm mr-1">add</span>
          Add Category
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/CategoryManager.tsx
git commit -m "feat: rewrite CategoryManager as sortable table with inline editing"
```

---

### Task 4: Verify and Polish

- [ ] **Step 1: Run backend tests**

Run: `uv run pytest tests/test_categories_api.py -v`
Expected: All PASS

- [ ] **Step 2: Run full backend test suite**

Run: `uv run pytest tests/ -v --ignore=tests/test_e2e.py`
Expected: No new failures

- [ ] **Step 3: Build frontend**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore: category manager polish and cleanup"
```
