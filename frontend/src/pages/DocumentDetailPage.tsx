import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import PageViewer from "@/components/PageViewer";
import MetadataForm from "@/components/MetadataForm";

const statusChipClass = (status: string) => {
  switch (status) {
    case "processed":    return "chip-processed";
    case "pending":
    case "processing":   return "chip-pending";
    case "failed":       return "chip-failed";
    case "needs_review": return "chip-review";
    default:             return "chip-pending";
  }
};

export default function DocumentDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<any>(null);

  useEffect(() => {
    if (id) api.get(`/documents/${id}`).then(setDoc);
  }, [id]);

  const goBack = useCallback(() => navigate("/documents"), [navigate]);
  const goPrev = useCallback(() => { if (doc?.prev_id) navigate(`/documents/${doc.prev_id}`); }, [doc, navigate]);
  const goNext = useCallback(() => { if (doc?.next_id) navigate(`/documents/${doc.next_id}`); }, [doc, navigate]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't intercept when typing in inputs
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "Escape") goBack();
      if (e.key === "ArrowLeft" || e.key === "k") goPrev();
      if (e.key === "ArrowRight" || e.key === "j") goNext();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goBack, goPrev, goNext]);

  if (!doc) return (
    <div className="flex items-center gap-3 text-muted-foreground">
      <span className="material-symbols-outlined animate-spin">progress_activity</span>
      Loading document...
    </div>
  );

  const handleSave = async (updates: any) => {
    const updated = await api.patch(`/documents/${id}`, updates);
    setDoc(updated);
  };

  const handleApprove = async () => {
    const updated = await api.patch(`/documents/${id}`, { status: "processed" });
    setDoc(updated);
  };

  const handleReprocess = async () => {
    await api.post(`/documents/${id}/reprocess`);
    const updated = await api.get(`/documents/${id}`);
    setDoc(updated);
  };

  const handleDelete = async () => {
    if (!confirm("Delete this document?")) return;
    await api.delete(`/documents/${id}`);
    navigate("/documents");
  };

  return (
    <div className="space-y-6">
      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <button
            onClick={goBack}
            className="p-2 hover:bg-card rounded-lg transition-colors"
            title="Back to list (Esc)"
          >
            <span className="material-symbols-outlined text-muted-foreground">arrow_back</span>
          </button>
          <div className="flex items-center gap-1">
            <button
              onClick={goPrev}
              disabled={!doc.prev_id}
              className="p-1.5 hover:bg-card rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Previous document (←)"
            >
              <span className="material-symbols-outlined text-sm text-muted-foreground">chevron_left</span>
            </button>
            <button
              onClick={goNext}
              disabled={!doc.next_id}
              className="p-1.5 hover:bg-card rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Next document (→)"
            >
              <span className="material-symbols-outlined text-sm text-muted-foreground">chevron_right</span>
            </button>
          </div>
          <div>
            <h1 className="text-xl font-headline font-bold text-primary truncate max-w-xs md:max-w-lg">
              {doc.vendor_name || doc.original_filename}
            </h1>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={statusChipClass(doc.status)}>{doc.status.replace("_", " ")}</span>
              {doc.document_type && (
                <span className="text-[10px] font-medium text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                  {doc.document_type.replace("_", " ")}
                </span>
              )}
              {doc.extraction_confidence != null && (
                <span className="text-[10px] font-medium text-muted-foreground">
                  {(doc.extraction_confidence * 100).toFixed(0)}% confidence
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex gap-2 flex-wrap">
          {doc.status === "needs_review" && (
            <Button
              onClick={handleApprove}
              className="bg-[#006d37] hover:bg-[#005228] text-white border-0 flex items-center gap-1.5"
            >
              <span className="material-symbols-outlined text-sm">check_circle</span>
              Approve
            </Button>
          )}
          <Button
            variant="outline"
            onClick={handleReprocess}
            className="flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">refresh</span>
            Reprocess
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            className="flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">delete</span>
            Delete
          </Button>
        </div>
      </div>

      {/* ── Split view ──────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Document preview */}
        <div className="bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-6">
          <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-4">Document Source</h3>
          <PageViewer docId={doc.id} pageCount={doc.page_count || 1} />
        </div>

        {/* Metadata form */}
        <div className="bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-6">
          <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-4">Core Metadata</h3>
          <MetadataForm doc={doc} onSave={handleSave} />
        </div>
      </div>

      {/* ── Edit history ────────────────────────────────────────────── */}
      {doc.edit_history && doc.edit_history.length > 0 && (
        <details className="bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] overflow-hidden">
          <summary className="flex items-center justify-between p-5 cursor-pointer hover:bg-muted transition-colors list-none">
            <div className="flex items-center gap-3">
              <span className="material-symbols-outlined text-primary">history</span>
              <span className="font-bold text-sm text-primary">Audit &amp; History</span>
              <span className="text-[10px] font-bold text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                {doc.edit_history.length} changes
              </span>
            </div>
            <span className="material-symbols-outlined toggle-icon text-muted-foreground transition-transform">expand_more</span>
          </summary>
          <div className="px-5 pb-5 border-t border-border">
            <div className="mt-4 space-y-3">
              {doc.edit_history.map((e: any, i: number) => (
                <div key={i} className="flex gap-3 items-start">
                  <div className="w-1 bg-primary/20 rounded-full self-stretch flex-shrink-0" />
                  <div className="flex-1">
                    <p className="text-xs font-bold text-primary">{e.field} changed</p>
                    <p className="text-[10px] text-muted-foreground">{e.timestamp}</p>
                    <p className="text-[11px] mt-0.5 text-muted-foreground">
                      From "{e.old_value}" → "{e.new_value}"
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
