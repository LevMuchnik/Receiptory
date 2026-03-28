import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, buttonVariants } from "@/components/ui/button";
import FilterBar, { type Filters, defaultFilters } from "@/components/FilterBar";
import DocumentTable from "@/components/DocumentTable";

export default function DocumentsPage() {
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [documents, setDocuments] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState("submission_date");
  const [sortOrder, setSortOrder] = useState("desc");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [quickFilter, setQuickFilter] = useState<string | null>(null);
  const pageSize = 20;

  const fetchDocs = useCallback(() => {
    const params = new URLSearchParams();
    if (filters.search) params.set("search", filters.search);
    if (filters.statuses.length > 0) params.set("status", filters.statuses.join(","));
    if (filters.category_ids.length > 0) params.set("category_id", filters.category_ids.join(","));
    if (filters.document_types.length > 0) params.set("document_type", filters.document_types.join(","));
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    if (quickFilter === "needs_review") params.set("status", "needs_review");
    if (quickFilter === "failed") params.set("status", "failed");
    if (quickFilter === "missing_info") params.set("missing_info", "true");
    params.set("sort_by", sortBy);
    params.set("sort_order", sortOrder);
    params.set("page", String(page));
    params.set("page_size", String(pageSize));

    api.get<{ documents: any[]; total: number }>(`/documents?${params}`).then((data) => {
      setDocuments(data.documents);
      setTotal(data.total);
    });
  }, [filters, page, sortBy, sortOrder, quickFilter]);

  useEffect(() => { fetchDocs(); }, [fetchDocs]);

  // Auto-refresh when documents are pending/processing
  useEffect(() => {
    const hasPending = documents.some((d) => d.status === "pending" || d.status === "processing");
    if (!hasPending) return;
    const interval = setInterval(fetchDocs, 3000);
    return () => clearInterval(interval);
  }, [documents, fetchDocs]);

  const handleSort = (field: string) => {
    if (sortBy === field) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortBy(field);
      setSortOrder("desc");
    }
  };

  const handleSelect = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    await api.upload(Array.from(e.target.files));
    fetchDocs();
    e.target.value = "";
  };

  const handleBatchReprocess = async () => {
    if (selected.size === 0) return;
    await api.post("/documents/batch-reprocess", { document_ids: Array.from(selected) });
    setSelected(new Set());
    fetchDocs();
  };

  const handleExportSelected = async () => {
    if (selected.size === 0) return;
    setExporting(true);
    try {
      const blob = await api.exportDocs({ document_ids: Array.from(selected) });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "receiptory_export.zip";
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  const handleBatchDelete = async () => {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} document(s)?`)) return;
    for (const id of selected) {
      await api.delete(`/documents/${id}`);
    }
    setSelected(new Set());
    fetchDocs();
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Documents</h1>
        <div className="flex gap-2">
          <label className={buttonVariants({ variant: "default" }) + " cursor-pointer"}>
            Upload
            <input type="file" multiple className="hidden" onChange={handleUpload} accept=".pdf,.jpg,.jpeg,.png,.html,.htm" />
          </label>
          {selected.size > 0 && (
            <>
              <Button variant="outline" onClick={handleExportSelected} disabled={exporting}>
                {exporting ? "Exporting..." : `Export (${selected.size})`}
              </Button>
              <Button variant="outline" onClick={handleBatchReprocess}>
                Reprocess ({selected.size})
              </Button>
              <Button variant="destructive" onClick={handleBatchDelete}>
                Delete ({selected.size})
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex gap-1">
        {[
          { key: null, label: "All" },
          { key: "needs_review", label: "Needs Review" },
          { key: "failed", label: "Failed" },
          { key: "missing_info", label: "Missing Info" },
        ].map((tab) => (
          <Button
            key={tab.key ?? "all"}
            variant={quickFilter === tab.key ? "default" : "outline"}
            size="sm"
            onClick={() => { setQuickFilter(tab.key); setPage(1); }}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <FilterBar filters={filters} onChange={(f) => { setFilters(f); setPage(1); }} />

      <DocumentTable
        documents={documents}
        selected={selected}
        onSelect={handleSelect}
        onSelectAll={() => {
          if (selected.size === documents.length) setSelected(new Set());
          else setSelected(new Set(documents.map((d) => d.id)));
        }}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={handleSort}
      />

      <div className="flex justify-between items-center">
        <span className="text-sm text-muted-foreground">{total} documents</span>
        <div className="flex gap-2">
          <Button variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <span className="text-sm py-2">Page {page} of {totalPages || 1}</span>
          <Button variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
        </div>
      </div>
    </div>
  );
}
