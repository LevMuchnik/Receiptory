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
  section: string | null;
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

  const expenseCats = categories.filter((c) => !c.is_system && c.section === "expense");
  const issuedCats = categories.filter((c) => !c.is_system && c.section === "issued");
  const systemCats = categories.filter((c) => c.is_system);
  const [activeSection, setActiveSection] = useState<"expense" | "issued">("expense");
  const userCats = activeSection === "expense" ? expenseCats : issuedCats;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const persistOrder = async (reordered: Category[]) => {
    const order = reordered.map((c, i) => ({ id: c.id, display_order: i }));
    await api.patch("/categories/reorder", { order });
  };

  const otherSectionCats = activeSection === "expense" ? issuedCats : expenseCats;

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = userCats.findIndex((c) => c.id === active.id);
    const newIndex = userCats.findIndex((c) => c.id === over.id);
    const reordered = arrayMove(userCats, oldIndex, newIndex);
    setCategories([...reordered, ...otherSectionCats, ...systemCats]);
    await persistOrder(reordered);
  };

  const handleMoveUp = async (id: number) => {
    const idx = userCats.findIndex((c) => c.id === id);
    if (idx <= 0) return;
    const reordered = arrayMove(userCats, idx, idx - 1);
    setCategories([...reordered, ...otherSectionCats, ...systemCats]);
    await persistOrder(reordered);
  };

  const handleMoveDown = async (id: number) => {
    const idx = userCats.findIndex((c) => c.id === id);
    if (idx < 0 || idx >= userCats.length - 1) return;
    const reordered = arrayMove(userCats, idx, idx + 1);
    setCategories([...reordered, ...otherSectionCats, ...systemCats]);
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
    await api.post("/categories", { name: newName, description: newDesc || null, section: activeSection });
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
      {/* Section tabs */}
      <div className="flex rounded-md border border-input overflow-hidden w-fit">
        <button
          type="button"
          onClick={() => setActiveSection("expense")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeSection === "expense"
              ? "bg-primary text-primary-foreground"
              : "bg-background hover:bg-accent"
          }`}
        >
          Expense Categories ({expenseCats.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveSection("issued")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeSection === "issued"
              ? "bg-blue-600 text-white"
              : "bg-background hover:bg-accent"
          }`}
        >
          Issued Document Types ({issuedCats.length})
        </button>
      </div>
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
