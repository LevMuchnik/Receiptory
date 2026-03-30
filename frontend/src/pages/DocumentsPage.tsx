import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { useFileDrop } from "@/lib/useFileDrop";
import { Button } from "@/components/ui/button";
import FilterBar, { type Filters, defaultFilters } from "@/components/FilterBar";
import DocumentTable from "@/components/DocumentTable";

export default function DocumentsPage() {
  const [searchParams] = useSearchParams();
  const urlSearch = searchParams.get("search") || "";
  const [filters, setFilters] = useState<Filters>({ ...defaultFilters, search: urlSearch });
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

  // Sync URL search param into filters
  useEffect(() => {
    if (urlSearch && urlSearch !== filters.search) {
      setFilters((f) => ({ ...f, search: urlSearch }));
    }
  }, [urlSearch]);

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

  const handleDropUpload = useCallback(async (files: File[]) => {
    await api.upload(files);
    fetchDocs();
  }, [fetchDocs]);
  const { dragging, ...dropHandlers } = useFileDrop(handleDropUpload);

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

  const handleBatchApprove = async () => {
    if (selected.size === 0) return;
    for (const id of selected) {
      await api.patch(`/documents/${id}`, { status: "processed" });
    }
    setSelected(new Set());
    fetchDocs();
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

  const QUICK_TABS = [
    { key: null,           label: "All" },
    { key: "needs_review", label: "Needs Review" },
    { key: "failed",       label: "Failed" },
    { key: "missing_info", label: "Missing Info" },
  ];

  return (
    <div className="space-y-6 relative" {...dropHandlers}>
      {/* ── Drop overlay ──────────────────────────────────────────────── */}
      {dragging && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-primary/10 backdrop-blur-sm pointer-events-none">
          <div className="bg-white rounded-2xl shadow-2xl p-10 flex flex-col items-center gap-3 border-2 border-dashed border-primary">
            <span className="material-symbols-outlined text-5xl text-primary">file_download</span>
            <p className="text-lg font-bold font-headline text-primary">Drop files to upload</p>
            <p className="text-sm text-[#43474c]">PDF, JPG, PNG, HTML supported</p>
          </div>
        </div>
      )}

      {/* ── Header ────────────────────────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-3xl font-headline font-extrabold tracking-tight text-primary">Document Browser</h2>
          <p className="text-[#43474c] font-medium">Manage and review extracted receipt data</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {selected.size > 0 && (
            <>
              <Button
                size="sm"
                className="bg-[#006d37] text-white hover:bg-[#005228] border-0 h-9"
                onClick={handleBatchApprove}
              >
                <span className="material-symbols-outlined text-sm mr-1">check_circle</span>
                Approve ({selected.size})
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-9"
                onClick={handleExportSelected}
                disabled={exporting}
              >
                <span className="material-symbols-outlined text-sm mr-1">file_download</span>
                {exporting ? "Exporting..." : `Export (${selected.size})`}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-9"
                onClick={handleBatchReprocess}
              >
                <span className="material-symbols-outlined text-sm mr-1">refresh</span>
                Reprocess
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="h-9"
                onClick={handleBatchDelete}
              >
                <span className="material-symbols-outlined text-sm mr-1">delete</span>
                Delete
              </Button>
            </>
          )}
          <label className="cursor-pointer h-9 px-4 rounded-lg text-sm font-semibold inline-flex items-center gap-1.5 text-white cta-gradient">
            <span className="material-symbols-outlined text-sm">upload_file</span>
            Upload
            <input type="file" multiple className="hidden" onChange={handleUpload} accept=".pdf,.jpg,.jpeg,.png,.html,.htm" />
          </label>
        </div>
      </div>

      {/* ── Quick filter tabs ────────────────────────────────────────── */}
      <div className="flex gap-2 flex-wrap">
        {QUICK_TABS.map((tab) => (
          <button
            key={tab.key ?? "all"}
            onClick={() => { setQuickFilter(tab.key); setPage(1); }}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
              quickFilter === tab.key
                ? "bg-primary text-white"
                : "bg-white text-[#43474c] hover:bg-[#e0e3e5]"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Filter bar ──────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-4">
        <FilterBar filters={filters} onChange={(f) => { setFilters(f); setPage(1); }} />
      </div>

      {/* ── Document table ──────────────────────────────────────────── */}
      <div className="bg-[#f2f4f6] rounded-2xl p-4 space-y-2">
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
      </div>

      {/* ── Pagination ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-[#43474c] font-medium">{total} document{total !== 1 ? "s" : ""}</span>
        <div className="flex gap-2 items-center">
          <button
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
            className="p-2 rounded-lg hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <span className="material-symbols-outlined text-[#43474c]">chevron_left</span>
          </button>
          <span className="text-sm font-medium text-[#191c1e] px-2">
            {page} / {totalPages || 1}
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage(page + 1)}
            className="p-2 rounded-lg hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <span className="material-symbols-outlined text-[#43474c]">chevron_right</span>
          </button>
        </div>
      </div>
    </div>
  );
}
