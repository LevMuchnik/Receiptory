import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import PageViewer from "@/components/PageViewer";
import MetadataForm from "@/components/MetadataForm";

export default function DocumentDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<any>(null);

  useEffect(() => {
    if (id) api.get(`/documents/${id}`).then(setDoc);
  }, [id]);

  if (!doc) return <div>Loading...</div>;

  const handleSave = async (updates: any) => {
    const updated = await api.patch(`/documents/${id}`, updates);
    setDoc(updated);
  };

  const handleReprocess = async () => {
    await api.post(`/documents/${id}/reprocess`);
    const updated = await api.get(`/documents/${id}`);
    setDoc(updated);
  };

  const handleDelete = async () => {
    await api.delete(`/documents/${id}`);
    navigate("/documents");
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-xl font-bold">{doc.original_filename}</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReprocess}>Reprocess</Button>
          <Button variant="destructive" onClick={handleDelete}>Delete</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <PageViewer docId={doc.id} pageCount={doc.page_count || 1} />
        <MetadataForm doc={doc} onSave={handleSave} />
      </div>

      {doc.edit_history && doc.edit_history.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Edit History ({doc.edit_history.length} changes)</summary>
          <div className="mt-2 space-y-1">
            {doc.edit_history.map((e: any, i: number) => (
              <div key={i} className="font-mono text-xs">
                {e.timestamp}: {e.field} changed from "{e.old_value}" to "{e.new_value}"
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
