/**
 * How a document's conversion into the reference currency (#72) reads in the
 * UI. The server already blanks a rate computed for another reference
 * currency, so a non-null converted_amount here is always current.
 */
export interface Convertible {
  total_amount: number | null;
  currency: string | null;
  converted_amount?: number | null;
  conversion_rate?: number | null;
  conversion_rate_date?: string | null;
  conversion_ref?: string | null;
}

export function formatAmount(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function sameCurrency(doc: Convertible): boolean {
  return !!doc.conversion_ref && (doc.currency || "").trim().toUpperCase() === doc.conversion_ref;
}

/** "≈ 307.80 ILS" under a foreign amount in the documents table, else null. */
export function convertedLabel(doc: Convertible): string | null {
  if (doc.converted_amount == null || !doc.conversion_ref || sameCurrency(doc)) return null;
  return `≈ ${formatAmount(doc.converted_amount)} ${doc.conversion_ref}`;
}

/** The read-only line on the document page: the conversion, or "No rate yet". */
export function conversionDetail(doc: Convertible): string | null {
  if (doc.total_amount == null || !doc.currency || sameCurrency(doc)) return null;
  if (doc.converted_amount == null || doc.conversion_rate == null) return "No rate yet";
  const rate = Number(doc.conversion_rate.toPrecision(4));
  const when = doc.conversion_rate_date ? ` (${doc.conversion_rate_date})` : "";
  return `≈ ${formatAmount(doc.converted_amount)} ${doc.conversion_ref} at ${rate}${when}`;
}
