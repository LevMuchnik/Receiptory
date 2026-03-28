import { useState, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

interface Category { id: number; name: string; }

interface Props {
  doc: any;
  onSave: (updates: any) => void;
}

export default function MetadataForm({ doc, onSave }: Props) {
  const [categories, setCategories] = useState<Category[]>([]);
  const [form, setForm] = useState({
    receipt_date: doc.receipt_date || "",
    vendor_name: doc.vendor_name || "",
    vendor_tax_id: doc.vendor_tax_id || "",
    vendor_receipt_id: doc.vendor_receipt_id || "",
    document_title: doc.document_title || "",
    client_name: doc.client_name || "",
    client_tax_id: doc.client_tax_id || "",
    description: doc.description || "",
    total_amount: doc.total_amount ?? "",
    currency: doc.currency || "",
    category_id: doc.category_id ? String(doc.category_id) : "",
    document_type: doc.document_type || "",
    user_notes: doc.user_notes || "",
  });

  useEffect(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  const update = (key: string, value: string) => setForm({ ...form, [key]: value });

  const handleSave = () => {
    const updates: any = {};
    for (const [key, val] of Object.entries(form)) {
      if (key === "total_amount" && val !== "") updates[key] = parseFloat(val as string);
      else if (key === "category_id" && val) updates[key] = parseInt(val as string);
      else if (val !== "") updates[key] = val;
    }
    onSave(updates);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Badge variant={doc.status === "processed" ? "default" : doc.status === "failed" ? "destructive" : "secondary"}>
          {doc.status}
        </Badge>
        {doc.extraction_confidence != null && (
          <span className="text-sm text-muted-foreground">Confidence: {(doc.extraction_confidence * 100).toFixed(0)}%</span>
        )}
        {doc.manually_edited && <Badge variant="outline">Edited</Badge>}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div><Label>Date</Label><Input value={form.receipt_date} onChange={(e) => update("receipt_date", e.target.value)} type="date" /></div>
        <div><Label>Vendor</Label><Input value={form.vendor_name} onChange={(e) => update("vendor_name", e.target.value)} /></div>
        <div><Label>Vendor Tax ID</Label><Input value={form.vendor_tax_id} onChange={(e) => update("vendor_tax_id", e.target.value)} /></div>
        <div><Label>Receipt/Invoice #</Label><Input value={form.vendor_receipt_id} onChange={(e) => update("vendor_receipt_id", e.target.value)} /></div>
        <div><Label>Title</Label><Input value={form.document_title} onChange={(e) => update("document_title", e.target.value)} /></div>
        <div><Label>Amount</Label><Input value={form.total_amount} onChange={(e) => update("total_amount", e.target.value)} type="number" step="0.01" /></div>
        <div><Label>Currency</Label><Input value={form.currency} onChange={(e) => update("currency", e.target.value)} /></div>
        <div>
          <Label>Type</Label>
          <Select value={form.document_type} onValueChange={(v) => update("document_type", v ?? "")}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="expense_receipt">Expense Receipt</SelectItem>
              <SelectItem value="issued_invoice">Issued Invoice</SelectItem>
              <SelectItem value="other_document">Other</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div><Label>Client</Label><Input value={form.client_name} onChange={(e) => update("client_name", e.target.value)} /></div>
        <div><Label>Client Tax ID</Label><Input value={form.client_tax_id} onChange={(e) => update("client_tax_id", e.target.value)} /></div>
        <div className="col-span-2">
          <Label>Category</Label>
          <Select value={form.category_id} onValueChange={(v) => update("category_id", v ?? "")}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {categories.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div><Label>Description</Label><Input value={form.description} onChange={(e) => update("description", e.target.value)} /></div>
      <div><Label>Notes</Label><Textarea value={form.user_notes} onChange={(e) => update("user_notes", e.target.value)} /></div>

      <Button onClick={handleSave}>Save Changes</Button>
    </div>
  );
}
