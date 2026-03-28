import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, buttonVariants } from "@/components/ui/button";
import FilterBar from "@/components/FilterBar";
import DocumentTable from "@/components/DocumentTable";

interface Filters {
  search: string;
  status: string;
  category_id: string;
  document_type: string;
  date_from: string;
  date_to: string;
}

const defaultFilters: Filters = { search: "", status: "all", category_id: "all", document_type: "all", date_from: "", date_to: "" };

export default function DocumentsPage() {
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [documents, setDocuments] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState("submission_date");
  const [sortOrder, setSortOrder] = useState("desc");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const pageSize = 20;

  const fetchDocs = useCallback(() => {
    const params = new URLSearchParams();
    if (filters.search) params.set("search", filters.search);
    if (filters.status !== "all") params.set("status", filters.status);
    if (filters.category_id !== "all") params.set("category_id", filters.category_id);
    if (filters.document_type !== "all") params.set("document_type", filters.document_type);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    params.set("sort_by", sortBy);
    params.set("sort_order", sortOrder);
    params.set("page", String(page));
    params.set("page_size", String(pageSize));

    api.get<{ documents: any[]; total: number }>(`/documents?${params}`).then((data) => {
      setDocuments(data.documents);
      setTotal(data.total);
    });
  }, [filters, page, sortBy, sortOrder]);

  useEffect(() => { fetchDocs(); }, [fetchDocs]);

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
            <Button variant="outline" onClick={handleBatchReprocess}>Reprocess ({selected.size})</Button>
          )}
        </div>
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
