import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

interface Category {
  id: number;
  name: string;
}

interface Filters {
  search: string;
  status: string;
  category_id: string;
  document_type: string;
  date_from: string;
  date_to: string;
}

interface FilterBarProps {
  filters: Filters;
  onChange: (filters: Filters) => void;
}

export default function FilterBar({ filters, onChange }: FilterBarProps) {
  const [categories, setCategories] = useState<Category[]>([]);

  useEffect(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  const update = (key: keyof Filters, value: string) => {
    onChange({ ...filters, [key]: value });
  };

  return (
    <div className="flex flex-wrap gap-2 items-end">
      <Input
        placeholder="Search..."
        value={filters.search}
        onChange={(e) => update("search", e.target.value)}
        className="w-48"
      />
      <Select value={filters.status} onValueChange={(v) => update("status", v ?? "all")}>
        <SelectTrigger className="w-36"><SelectValue placeholder="Status" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All statuses</SelectItem>
          <SelectItem value="pending">Pending</SelectItem>
          <SelectItem value="processed">Processed</SelectItem>
          <SelectItem value="needs_review">Needs Review</SelectItem>
          <SelectItem value="failed">Failed</SelectItem>
        </SelectContent>
      </Select>
      <Select value={filters.category_id} onValueChange={(v) => update("category_id", v ?? "all")}>
        <SelectTrigger className="w-36"><SelectValue placeholder="Category" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All categories</SelectItem>
          {categories.map((c) => (
            <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={filters.document_type} onValueChange={(v) => update("document_type", v ?? "all")}>
        <SelectTrigger className="w-40"><SelectValue placeholder="Type" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All types</SelectItem>
          <SelectItem value="expense_receipt">Expense Receipt</SelectItem>
          <SelectItem value="issued_invoice">Issued Invoice</SelectItem>
          <SelectItem value="other_document">Other</SelectItem>
        </SelectContent>
      </Select>
      <Input type="date" value={filters.date_from} onChange={(e) => update("date_from", e.target.value)} className="w-36" />
      <Input type="date" value={filters.date_to} onChange={(e) => update("date_to", e.target.value)} className="w-36" />
      <Button variant="outline" onClick={() => onChange({ search: "", status: "all", category_id: "all", document_type: "all", date_from: "", date_to: "" })}>
        Clear
      </Button>
    </div>
  );
}
