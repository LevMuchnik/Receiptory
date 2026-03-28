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
    subtotal: doc.subtotal ?? "",
    tax_amount: doc.tax_amount ?? "",
    currency: doc.currency || "",
    category_id: doc.category_id ? String(doc.category_id) : "",
    document_type: doc.document_type || "",
    payment_method: doc.payment_method || "",
    payment_identifier: doc.payment_identifier || "",
    user_notes: doc.user_notes || "",
  });

  useEffect(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  const update = (key: string, value: string) => setForm({ ...form, [key]: value });

  const handleSave = () => {
    const updates: any = {};
    for (const [key, val] of Object.entries(form)) {
      if (["total_amount", "subtotal", "tax_amount"].includes(key) && val !== "") updates[key] = parseFloat(val as string);
      else if (key === "category_id" && val) updates[key] = parseInt(val as string);
      else if (val !== "") updates[key] = val;
    }
    onSave(updates);
  };

  const categoryName = categories.find(c => String(c.id) === form.category_id)?.name;

  return (
    <div className="space-y-4">
      {/* Status bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant={doc.status === "processed" ? "default" : doc.status === "failed" ? "destructive" : "secondary"}>
          {doc.status}
        </Badge>
        {doc.document_type && (
          <Badge variant="outline">{doc.document_type.replace("_", " ")}</Badge>
        )}
        {doc.extraction_confidence != null && (
          <span className="text-sm text-muted-foreground">Confidence: {(doc.extraction_confidence * 100).toFixed(0)}%</span>
        )}
        {doc.manually_edited && <Badge variant="outline">Edited</Badge>}
        {doc.language && (
          <span className="text-sm text-muted-foreground">Lang: {doc.language}</span>
        )}
      </div>

      {/* Core fields */}
      <div className="grid grid-cols-2 gap-3">
        <div><Label>Document Date</Label><Input value={form.receipt_date} onChange={(e) => update("receipt_date", e.target.value)} type="date" /></div>
        <div><Label>Document Title</Label><Input value={form.document_title} onChange={(e) => update("document_title", e.target.value)} /></div>
        <div><Label>Vendor</Label><Input value={form.vendor_name} onChange={(e) => update("vendor_name", e.target.value)} /></div>
        <div><Label>Vendor Tax ID</Label><Input value={form.vendor_tax_id} onChange={(e) => update("vendor_tax_id", e.target.value)} /></div>
        <div><Label>Receipt/Invoice #</Label><Input value={form.vendor_receipt_id} onChange={(e) => update("vendor_receipt_id", e.target.value)} /></div>
        <div>
          <Label>Category{categoryName ? `: ${categoryName}` : ""}</Label>
          <Select value={form.category_id} onValueChange={(v) => update("category_id", v ?? "")}>
            <SelectTrigger><SelectValue placeholder="Select category" /></SelectTrigger>
            <SelectContent>
              {categories.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label>Document Type</Label>
          <Select value={form.document_type} onValueChange={(v) => update("document_type", v ?? "")}>
            <SelectTrigger><SelectValue placeholder="Select type" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="expense_receipt">Expense Receipt</SelectItem>
              <SelectItem value="issued_invoice">Issued Invoice</SelectItem>
              <SelectItem value="other_document">Other</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div><Label>Client</Label><Input value={form.client_name} onChange={(e) => update("client_name", e.target.value)} /></div>
        <div><Label>Client Tax ID</Label><Input value={form.client_tax_id} onChange={(e) => update("client_tax_id", e.target.value)} /></div>
      </div>

      <div><Label>Description</Label><Input value={form.description} onChange={(e) => update("description", e.target.value)} /></div>

      {/* Amounts */}
      <details open>
        <summary className="cursor-pointer font-medium text-sm mb-2">Amounts</summary>
        <div className="grid grid-cols-3 gap-3">
          <div><Label>Subtotal</Label><Input value={form.subtotal} onChange={(e) => update("subtotal", e.target.value)} type="number" step="0.01" /></div>
          <div><Label>Tax</Label><Input value={form.tax_amount} onChange={(e) => update("tax_amount", e.target.value)} type="number" step="0.01" /></div>
          <div><Label>Total</Label><Input value={form.total_amount} onChange={(e) => update("total_amount", e.target.value)} type="number" step="0.01" /></div>
          <div><Label>Currency</Label><Input value={form.currency} onChange={(e) => update("currency", e.target.value)} /></div>
          <div><Label>Payment Method</Label><Input value={form.payment_method} onChange={(e) => update("payment_method", e.target.value)} /></div>
          <div><Label>Payment ID (last digits, etc.)</Label><Input value={form.payment_identifier} onChange={(e) => update("payment_identifier", e.target.value)} /></div>
        </div>
      </details>

      {/* Line items (read-only) */}
      {doc.line_items && doc.line_items.length > 0 && (
        <details>
          <summary className="cursor-pointer font-medium text-sm mb-2">Line Items ({doc.line_items.length})</summary>
          <div className="border rounded p-2 text-sm space-y-1">
            {doc.line_items.map((item: any, i: number) => (
              <div key={i} className="flex justify-between">
                <span>{item.description}</span>
                <span className="font-mono">
                  {item.quantity != null ? `${item.quantity} x ` : ""}
                  {item.unit_price != null ? item.unit_price.toFixed(2) : ""}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Additional fields (read-only) */}
      {doc.additional_fields && doc.additional_fields.length > 0 && (
        <details>
          <summary className="cursor-pointer font-medium text-sm mb-2">Additional Fields ({doc.additional_fields.length})</summary>
          <div className="border rounded p-2 text-sm space-y-1">
            {doc.additional_fields.map((f: any, i: number) => (
              <div key={i} className="flex justify-between">
                <span className="text-muted-foreground">{f.key}</span>
                <span>{f.value}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Ingestion & Processing metadata (read-only) */}
      <details>
        <summary className="cursor-pointer font-medium text-sm mb-2">Ingestion & Processing</summary>
        <div className="border rounded p-2 text-sm space-y-1">
          <div className="flex justify-between"><span className="text-muted-foreground">Upload Date</span><span>{doc.submission_date || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Channel</span><span>{doc.submission_channel || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Sender</span><span>{doc.sender_identifier || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Original File</span><span>{doc.original_filename || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Stored As</span><span>{doc.stored_filename || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">File Hash</span><span className="font-mono text-xs">{doc.file_hash || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">File Size</span><span>{doc.file_size_bytes ? `${(doc.file_size_bytes / 1024).toFixed(1)} KB` : "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Pages</span><span>{doc.page_count ?? "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">LLM Model</span><span>{doc.processing_model || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Tokens (in/out)</span><span>{doc.processing_tokens_in ?? "-"} / {doc.processing_tokens_out ?? "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Cost</span><span>{doc.processing_cost_usd != null ? `$${doc.processing_cost_usd.toFixed(4)}` : "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Processed At</span><span>{doc.processing_date || "-"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Attempts</span><span>{doc.processing_attempts ?? 0}</span></div>
          {doc.processing_error && <div className="flex justify-between"><span className="text-muted-foreground">Error</span><span className="text-destructive">{doc.processing_error}</span></div>}
          <div className="flex justify-between"><span className="text-muted-foreground">Last Exported</span><span>{doc.last_exported_date || "Never"}</span></div>
        </div>
      </details>

      {/* OCR text (read-only) */}
      {doc.raw_extracted_text && (
        <details>
          <summary className="cursor-pointer font-medium text-sm mb-2">OCR Text</summary>
          <pre className="border rounded p-2 text-xs whitespace-pre-wrap max-h-48 overflow-auto">{doc.raw_extracted_text}</pre>
        </details>
      )}

      {/* Notes */}
      <div><Label>Notes</Label><Textarea value={form.user_notes} onChange={(e) => update("user_notes", e.target.value)} /></div>

      <Button onClick={handleSave}>Save Changes</Button>
    </div>
  );
}
