import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

interface Category {
  id: number;
  name: string;
  description: string | null;
  is_system: boolean;
}

export default function CategoryManager() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const load = () => api.get<Category[]>("/categories").then(setCategories);
  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!newName) return;
    await api.post("/categories", { name: newName, description: newDesc || null });
    setNewName(""); setNewDesc("");
    load();
  };

  const handleUpdate = async (id: number) => {
    await api.patch(`/categories/${id}`, { name: editName, description: editDesc });
    setEditingId(null);
    load();
  };

  const handleDelete = async (id: number) => {
    await api.delete(`/categories/${id}`);
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input placeholder="Name" value={newName} onChange={(e) => setNewName(e.target.value)} className="w-40" />
        <Input placeholder="Description" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} className="flex-1" />
        <Button onClick={handleCreate}>Add</Button>
      </div>
      <div className="space-y-2">
        {categories.map((c) => (
          <div key={c.id} className="flex items-center gap-2 p-2 border rounded">
            {editingId === c.id ? (
              <>
                <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="w-40" />
                <Input value={editDesc} onChange={(e) => setEditDesc(e.target.value)} className="flex-1" />
                <Button size="sm" onClick={() => handleUpdate(c.id)}>Save</Button>
                <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>Cancel</Button>
              </>
            ) : (
              <>
                <span className="font-medium">{c.name}</span>
                {c.is_system && <Badge variant="secondary">system</Badge>}
                <span className="text-sm text-muted-foreground flex-1">{c.description || ""}</span>
                {!c.is_system && (
                  <>
                    <Button size="sm" variant="outline" onClick={() => { setEditingId(c.id); setEditName(c.name); setEditDesc(c.description || ""); }}>Edit</Button>
                    <Button size="sm" variant="destructive" onClick={() => handleDelete(c.id)}>Delete</Button>
                  </>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
