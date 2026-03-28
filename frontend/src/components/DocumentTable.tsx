import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
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

export default function DocumentTable({ documents, selected, onSelect, onSelectAll, sortBy, sortOrder, onSort }: Props) {
  const sortIcon = (field: string) => {
    if (sortBy !== field) return "";
    return sortOrder === "asc" ? " \u2191" : " \u2193";
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8">
            <Checkbox checked={selected.size === documents.length && documents.length > 0} onCheckedChange={onSelectAll} />
          </TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("receipt_date")}>Date{sortIcon("receipt_date")}</TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("vendor_name")}>Vendor{sortIcon("vendor_name")}</TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("total_amount")}>Amount{sortIcon("total_amount")}</TableHead>
          <TableHead>Category</TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("status")}>Status{sortIcon("status")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => (
          <TableRow key={doc.id} className="cursor-pointer" onClick={() => window.location.href = `/documents/${doc.id}`}>
            <TableCell onClick={(e) => e.stopPropagation()}>
              <Checkbox checked={selected.has(doc.id)} onCheckedChange={() => onSelect(doc.id)} />
            </TableCell>
            <TableCell>{doc.receipt_date || "-"}</TableCell>
            <TableCell>{doc.vendor_name || doc.original_filename}</TableCell>
            <TableCell className="font-mono">{doc.total_amount != null ? `${doc.total_amount.toFixed(2)} ${doc.currency || ""}` : "-"}</TableCell>
            <TableCell>{doc.category_name || "-"}</TableCell>
            <TableCell>
              <Badge variant={doc.status === "processed" ? "default" : doc.status === "failed" ? "destructive" : "secondary"}>
                {doc.status}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
