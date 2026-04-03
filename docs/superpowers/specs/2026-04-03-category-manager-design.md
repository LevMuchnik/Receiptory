# Category Manager Redesign

**Date:** 2026-04-03
**Status:** Draft

## Problem

The current category management UI uses a card-based list that doesn't align columns (name and description are not visually aligned), has no way to reorder categories, and only supports clunky one-at-a-time editing. With 27+ categories, this is hard to scan and manage.

## Goals

- Replace card-based list with a proper data table with aligned columns
- Support drag-and-drop reordering with up/down arrow fallback
- Enable inline editing of name and description by clicking cells
- Show document count per category for context
- Persist display order to the backend

## Non-Goals

- Hierarchical/nested categories
- Category merging or bulk operations
- Category icons or colors

---

## Design

### Table Layout

Full-width table with 5 columns:

| Column | Width | Content |
|--------|-------|---------|
| Drag handle | 40px | Grip dots icon (⠿). Not shown for system categories. |
| Name | ~200px | Category name. Click to inline edit. |
| Description | flex | Category description. Click to inline edit. |
| Docs | 60px | Read-only count of documents assigned to this category. |
| Actions | 100px | Up/down arrow buttons + delete button. |

System categories (pending, not_a_receipt, failed, unauthorized_sender) are shown in a separate "System" section below the user categories. They are non-editable, non-draggable, and visually grayed out.

### Inline Editing

- Single-click on a name or description cell converts it to a text input with the current value.
- Save: blur or Enter key. Calls `PATCH /categories/{id}` with the updated field.
- Cancel: Escape key reverts to the previous value.
- Only one cell is editable at a time (clicking another cell saves the current one first).
- Name is required — empty name on blur reverts to previous value.

### Reordering

**Drag-and-drop:**
- Grab the drag handle on the left of any user category row.
- Drag to a new position within the user categories section.
- On drop: visually reorder, then call `PATCH /categories/reorder` with the new order.
- Uses `@dnd-kit/sortable` library (~15KB, well-maintained).

**Arrow buttons:**
- Up arrow: swap this row with the row above it.
- Down arrow: swap this row with the row below it.
- First row: up arrow disabled. Last row: down arrow disabled.
- On click: immediately swap and persist via `PATCH /categories/reorder`.

### Adding a Category

- "Add Category" button below the table.
- Clicking it appends a new row at the bottom of the user categories in edit mode.
- Name field is focused. Type name (required) and description.
- Save: blur or Enter on the last field. Calls `POST /categories`.
- Cancel: Escape removes the unsaved row.

### Deleting a Category

- Delete button (trash icon) in the actions column.
- Confirmation prompt: "Delete {name}?"
- Calls `DELETE /categories/{id}` (soft delete).
- System categories have no delete button.

---

## Backend Changes

### New Endpoint: `PATCH /categories/reorder`

Batch-update display order for all user categories in a single request.

**Request body:**
```json
{
  "order": [
    {"id": 10, "display_order": 0},
    {"id": 13, "display_order": 1},
    {"id": 14, "display_order": 2}
  ]
}
```

**Response:** 200 OK with updated category list.

**Implementation:** Single transaction updating `display_order` for each category ID.

### Modify: `GET /categories`

Add `document_count` to each category in the response. Use a LEFT JOIN with the documents table:

```sql
SELECT c.*, COUNT(d.id) AS document_count
FROM categories c
LEFT JOIN documents d ON d.category_id = c.id AND d.is_deleted = 0
WHERE c.is_deleted = 0
GROUP BY c.id
ORDER BY c.is_system ASC, c.display_order ASC, c.name ASC
```

### No Changes to Existing Endpoints

- `POST /categories` — unchanged
- `PATCH /categories/{id}` — unchanged (already supports name, description, display_order)
- `DELETE /categories/{id}` — unchanged

---

## Frontend Changes

### Dependencies

Add `@dnd-kit/core` and `@dnd-kit/sortable` to frontend package.json.

### Component: `CategoryManager.tsx`

Complete rewrite of the existing component. Key state:

- `categories`: array from API (with document_count)
- `editingCell`: `{id: number, field: 'name' | 'description'} | null`
- `editValue`: string (current input value)
- `newRow`: boolean (whether an unsaved new row is being added)
- `newName` / `newDescription`: string (new row input values)

### Rendering

- `@dnd-kit/sortable` `SortableContext` wraps the user category rows.
- Each row is a `SortableItem` with a drag handle.
- System categories rendered separately below, outside the sortable context.

---

## Error Handling

- Inline edit save failure: revert cell to previous value, show brief toast/error.
- Reorder failure: revert to previous order, show error.
- Delete failure: show error, row remains.
- Add failure: show error, remove unsaved row.
- Network errors: all operations are optimistic with rollback on failure.
