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
  // Rate 1 means the server matched the currency to the reference, aliases
  // included (the model can write "NIS" for ILS).
  if (!doc.conversion_ref) return false;
  return doc.conversion_rate === 1 || (doc.currency || "").trim().toUpperCase() === doc.conversion_ref;
}

/** The converted amount under a foreign amount in the documents table, split
 *  so the number never breaks across lines in a narrow column; else null. */
export function convertedLabel(doc: Convertible): { amount: string; ref: string } | null {
  if (doc.converted_amount == null || !doc.conversion_ref || sameCurrency(doc)) return null;
  return { amount: `≈ ${formatAmount(doc.converted_amount)}`, ref: doc.conversion_ref };
}

/** The read-only line on the document page: the conversion with the rate's
 *  direction and date, or why the document is missing from converted totals. */
export function conversionDetail(doc: Convertible): string | null {
  if (doc.total_amount == null || !doc.currency || sameCurrency(doc)) return null;
  if (doc.converted_amount == null || doc.conversion_rate == null || !doc.conversion_ref) {
    return "No exchange rate yet: not included in converted totals";
  }
  const rate = Number(doc.conversion_rate.toPrecision(4));
  const code = doc.currency.trim().toUpperCase();
  const when = doc.conversion_rate_date ? `, rate of ${doc.conversion_rate_date}` : "";
  return `≈ ${formatAmount(doc.converted_amount)} ${doc.conversion_ref} (1 ${code} = ${rate} ${doc.conversion_ref}${when})`;
}
