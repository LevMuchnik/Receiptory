import { useNavigate } from "react-router-dom";
import { Checkbox } from "@/components/ui/checkbox";

interface Document {
  id: number;
  receipt_date: string | null;
  vendor_name: string | null;
  total_amount: number | null;
  currency: string | null;
  status: string;
  category_name: string | null;
  original_filename: string;
  submission_date: string;
}

interface Props {
  documents: Document[];
  selected: Set<number>;
  onSelect: (id: number) => void;
  onSelectAll: () => void;
  sortBy: string;
  sortOrder: string;
  onSort: (field: string) => void;
}

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

const statusIconName = (status: string) => {
  switch (status) {
    case "processed":    return "check_circle";
    case "failed":       return "error";
    case "needs_review": return "warning";
    default:             return "schedule";
  }
};

export default function DocumentTable({ documents, selected, onSelect, onSelectAll, sortBy, sortOrder, onSort }: Props) {
  const navigate = useNavigate();
  const sortIcon = (field: string) => {
    if (sortBy !== field) return <span className="material-symbols-outlined text-sm text-[#c4c6cd]">unfold_more</span>;
    return <span className="material-symbols-outlined text-sm text-primary">{sortOrder === "asc" ? "arrow_upward" : "arrow_downward"}</span>;
  };

  if (documents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <span className="material-symbols-outlined text-5xl text-[#c4c6cd] mb-3">folder_open</span>
        <p className="font-semibold">No documents found</p>
        <p className="text-sm mt-1">Try adjusting your filters</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Column headers */}
      <div className="grid grid-cols-12 gap-3 px-4 py-2 text-[11px] font-bold text-slate-500 uppercase tracking-widest">
        <div className="col-span-1 flex items-center">
          <Checkbox
            checked={selected.size === documents.length && documents.length > 0}
            onCheckedChange={onSelectAll}
          />
        </div>
        <div className="col-span-4 flex items-center gap-1 cursor-pointer select-none" onClick={() => onSort("vendor_name")}>
          Vendor &amp; Date {sortIcon("vendor_name")}
        </div>
        <div className="col-span-2 flex items-center gap-1 cursor-pointer select-none" onClick={() => onSort("total_amount")}>
          Amount {sortIcon("total_amount")}
        </div>
        <div className="col-span-2 flex items-center gap-1 cursor-pointer select-none" onClick={() => onSort("status")}>
          Status {sortIcon("status")}
        </div>
        <div className="col-span-2">Category</div>
        <div className="col-span-1 flex items-center gap-1 cursor-pointer select-none justify-end" onClick={() => onSort("receipt_date")}>
          Date {sortIcon("receipt_date")}
        </div>
      </div>

      {/* Rows */}
      {documents.map((doc) => (
        <div
          key={doc.id}
          className="grid grid-cols-12 gap-3 items-center bg-card px-4 py-3 rounded-xl border border-transparent hover:border-primary/10 transition-all group cursor-pointer shadow-[0_1px_4px_rgba(25,28,30,0.04)]"
          onClick={() => navigate(`/documents/${doc.id}`)}
        >
          {/* Checkbox */}
          <div className="col-span-1 flex items-center" onClick={(e) => e.stopPropagation()}>
            <Checkbox
              checked={selected.has(doc.id)}
              onCheckedChange={() => onSelect(doc.id)}
            />
          </div>

          {/* Vendor & date */}
          <div className="col-span-4 flex items-center gap-3">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${
              doc.status === "failed" ? "bg-[#ffdad6] text-[#93000a]" : "bg-muted text-muted-foreground"
            }`}>
              <span className="material-symbols-outlined text-base">{statusIconName(doc.status)}</span>
            </div>
            <div className="min-w-0">
              <p className="font-bold text-primary text-sm truncate">{doc.vendor_name || doc.original_filename}</p>
              <p className="text-xs text-muted-foreground">{doc.receipt_date || doc.submission_date || "—"}</p>
            </div>
          </div>

          {/* Amount */}
          <div className="col-span-2">
            <p className="font-headline font-bold text-sm">
              {doc.total_amount != null ? doc.total_amount.toFixed(2) : "—"}
            </p>
            <p className="text-[10px] text-slate-400 font-medium">{doc.currency || ""}</p>
          </div>

          {/* Status chip */}
          <div className="col-span-2">
            <span className={statusChipClass(doc.status)}>{doc.status.replace("_", " ")}</span>
          </div>

          {/* Category */}
          <div className="col-span-2">
            {doc.category_name ? (
              <span className="text-xs font-medium text-muted-foreground px-2 py-1 bg-muted rounded-md">{doc.category_name}</span>
            ) : (
              <span className="text-xs text-slate-400">—</span>
            )}
          </div>

          {/* Open icon */}
          <div className="col-span-1 text-right">
            <button
              className="p-1.5 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-muted rounded-lg"
              onClick={(e) => { e.stopPropagation(); navigate(`/documents/${doc.id}`); }}
            >
              <span className="material-symbols-outlined text-muted-foreground hover:text-primary text-base">open_in_new</span>
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
