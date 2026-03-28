import { useEffect, useState, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { api } from "@/lib/api";

interface Category {
  id: number;
  name: string;
}

export interface Filters {
  search: string;
  statuses: string[];
  category_ids: string[];
  document_types: string[];
  date_from: string;
  date_to: string;
}

export const defaultFilters: Filters = {
  search: "",
  statuses: [],
  category_ids: [],
  document_types: [],
  date_from: "",
  date_to: "",
};

interface FilterBarProps {
  filters: Filters;
  onChange: (filters: Filters) => void;
}

const STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "processing", label: "Processing" },
  { value: "processed", label: "Processed" },
  { value: "needs_review", label: "Needs Review" },
  { value: "failed", label: "Failed" },
];

const TYPE_OPTIONS = [
  { value: "expense_receipt", label: "Expense Receipt" },
  { value: "issued_invoice", label: "Issued Invoice" },
  { value: "other_document", label: "Other" },
];

function MultiSelect({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const toggle = (value: string) => {
    onChange(
      selected.includes(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value]
    );
  };

  const summary =
    selected.length === 0
      ? `All ${label}`
      : selected.length === options.length
        ? `All ${label}`
        : `${label} (${selected.length})`;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex h-9 w-40 items-center justify-between rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
      >
        <span className="truncate">{summary}</span>
        <span className="text-xs ml-1">{open ? "\u25B2" : "\u25BC"}</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-48 rounded-md border bg-background shadow-md p-1">
          <button
            type="button"
            className="w-full text-left px-2 py-1 text-xs text-muted-foreground hover:bg-accent rounded"
            onClick={() => onChange([])}
          >
            All (clear filters)
          </button>
          <button
            type="button"
            className="w-full text-left px-2 py-1 text-xs text-muted-foreground hover:bg-accent rounded"
            onClick={() => onChange(options.map((o) => o.value))}
          >
            Select all
          </button>
          <div className="border-t my-1" />
          {options.map((opt) => (
            <label
              key={opt.value}
              className="flex items-center gap-2 px-2 py-1 hover:bg-accent rounded cursor-pointer"
            >
              <Checkbox
                checked={selected.includes(opt.value)}
                onCheckedChange={() => toggle(opt.value)}
              />
              <span className="text-sm">{opt.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export default function FilterBar({ filters, onChange }: FilterBarProps) {
  const [categories, setCategories] = useState<Category[]>([]);

  useEffect(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  const categoryOptions = categories.map((c) => ({
    value: String(c.id),
    label: c.name,
  }));

  return (
    <div className="flex flex-wrap gap-2 items-end">
      <Input
        placeholder="Search..."
        value={filters.search}
        onChange={(e) => onChange({ ...filters, search: e.target.value })}
        className="w-48"
      />
      <MultiSelect
        label="Status"
        options={STATUS_OPTIONS}
        selected={filters.statuses}
        onChange={(statuses) => onChange({ ...filters, statuses })}
      />
      <MultiSelect
        label="Category"
        options={categoryOptions}
        selected={filters.category_ids}
        onChange={(category_ids) => onChange({ ...filters, category_ids })}
      />
      <MultiSelect
        label="Type"
        options={TYPE_OPTIONS}
        selected={filters.document_types}
        onChange={(document_types) => onChange({ ...filters, document_types })}
      />
      <Input
        type="date"
        value={filters.date_from}
        onChange={(e) => onChange({ ...filters, date_from: e.target.value })}
        className="w-36"
      />
      <Input
        type="date"
        value={filters.date_to}
        onChange={(e) => onChange({ ...filters, date_to: e.target.value })}
        className="w-36"
      />
      <Button
        variant="outline"
        onClick={() => onChange(defaultFilters)}
      >
        Clear
      </Button>
    </div>
  );
}
