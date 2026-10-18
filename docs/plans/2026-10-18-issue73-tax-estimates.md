# Issue #73: Taxes tab, monthly income-tax and VAT estimates

**Status:** plan, reviewed with Astra; build not started · **Branch:** `issue/73-tax-estimates` · **Depends on:** #72 (currency conversion) · **Related:** #63 (misdated documents)

Written in a Claude Code session in the Smelter repo, from a brainstorm with the owner and Astra (gpt-6-astra). Receiptory's agent builds it, and starts with Stage 0 on the live database.

## Goal

The dashboard answers "how much income tax and VAT do I pay this month?" at a glance, and the same for any past month. The numbers are **estimates** computed from issued documents. Exact amounts aren't the goal.

**Non-goals**
- A VAT return. No input VAT from expenses: the VAT figure is an upper limit.
- Tax withheld at source (ניכוי במקור), national insurance (ביטוח לאומי), and payments actually made.
- Exact due dates (the 15th, the 19th online, holidays) and a payment calendar.
- The invoice4u API. Documents keep arriving by email and Telegram.
- Rate and frequency history: the current settings apply to every month shown (see D7).

## Decisions

### The owner's
1. A **Taxes** tab next to "Expense Distribution", in the same panel. Taxes comes first and is open by default.
2. **Income tax** is paid monthly, on the previous month's income: a fixed percentage of the **pre-VAT** income. The rate is set in Settings (the owner's is 45%).
3. **VAT** is paid bi-monthly by default (Jan–Feb, Mar–Apr, ...) or monthly, set in Settings. Show the **upper limit**, computed from income only. No VAT return.
4. Step back to see the estimates for past months.
5. Understand each issued document, don't double-count one income event, and know which documents are tax events. Hebrew and English.
6. Documents come from invoice4u by email and Telegram. No invoice4u API; an extra LLM call is fine.
7. Foreign currency converts with Bank of Israel rates for the document's date, cached (#72).
8. Estimates are enough.
9. When something is unclear, show the estimate anyway, with a "documents need a look" badge. Don't hold the number back.
10. Receiptory's agent runs the data survey (Stage 0) on the live database before building.

### Design choices (each with the alternative rejected)
- **D1. What counts.** A tax invoice and a tax invoice-receipt count on their own date. A credit note counts negative on its date. A receipt, a demand for payment (חשבון עסקה), a quote and a work order never count. *Rejected:* recognising income tax on the payment date, which would need receipt-to-invoice matching and a second rule. For an estimate the invoice date is enough: invoice4u users on a cash basis issue the tax document (usually a חשבונית מס קבלה) when paid. The same rule also means a demand and its tax invoice can never both count, so no matching is needed to avoid that.
- **D2. Classification with its own LLM call, on issued documents only.** A second, text-only call gets the document's kind and the numbers it cites. *Rejected:* adding fields to extraction's `_FIELD_SCHEMAS`. Field order there is load-bearing, and every extraction change has to pass the `compare_json_mode.py` merge gate. Also rejected: deriving the kind from the category, which is a label the owner can rename.
- **D3. Duplicates are copies of one document.** Copies (a Hebrew and an English version, original and copy, a re-scan with a new hash) share a kind and a document number. They count once. *Rejected:* fuzzy event matching (same client and amount within 60 days). It merges legitimate repeats, such as monthly retainers or several instalments billed against one demand.
- **D4. References are for display only.** The numbers a document cites (e.g. "בהתאם לחשבון עסקה 1023") group related documents in the drill-down and never remove an amount. A credit note cites the invoice it corrects, and both still count.
- **D5. The VAT figure is the stated VAT.** It is the sum of the `tax_amount` printed on the counted documents, converted to ILS. *Rejected:* rate × income, which gets zero-rated export invoices and the 17%→18% change wrong.
- **D6. Missing isn't zero.** A counted document missing its pre-VAT amount, VAT, date, kind or conversion is left out of the sum it can't support and listed under "needs a look". The line says how many it leaves out. A stated VAT of 0 is a real value and counts as 0.
- **D7. Settings have no history.** One rate and one frequency apply to every month shown, and the tab says so. *Rejected:* dated settings ("from 2026-01: 45%"). The owner wants estimates, so a past month shifting after a rate change is acceptable, and dated settings are a second mechanism for a rare event.
- **D8. Computed when viewed, never stored.** Past months are recomputed from the current documents, so late uploads and corrections show up. *Rejected:* monthly snapshots, which add a write path and a stale-data problem for no gain in an estimate.

## Design

### Month arithmetic
The tab shows a **payment month** M (default: the current month).
- **Income tax payable in M** = rate × pre-VAT income dated in M−1. The tab also shows "accruing for next month": rate × income dated in M so far.
- **VAT, monthly:** the period is M−1.
- **VAT, bi-monthly:** the periods are Jan–Feb, Mar–Apr, May–Jun, Jul–Aug, Sep–Oct, Nov–Dec. Payment falls in the month after a period ends: Mar, May, Jul, Sep, Nov and Jan. Jan pays for Nov–Dec of the previous year.
  - In a payment month, the period is (M−2, M−1).
  - In any other month, the tab says "No scheduled VAT payment this month" and shows the running period (M−1, M) so far.

### Per-document data (Stage 1)
Two new columns on `documents`, filled only for `document_type = 'issued_invoice'`:
- `issued_kind`: one of `tax_invoice`, `tax_invoice_receipt`, `receipt`, `credit_note`, `payment_demand`, `quote`, `work_order`, `other` or `unknown`. NULL means not classified yet.
- `referenced_numbers`: a JSON array of strings, the document numbers this one cites.

`backend/processing/issued_kind.py` holds `classify_issued(document_title, raw_extracted_text, model, api_key, ...)`, which returns `(kind, referenced_numbers, tokens_in, tokens_out)`.
- **The call:** text only (`document_title` + `raw_extracted_text`; no images). It requests a schema through the shared helpers in `extract.py` (`strict_object_schema`, `response_format_kwargs`, `complete_with_schema_fallback`), the same way `url_triage` does.
- **The prompt names both languages' markers:**
  - חשבונית מס / Tax Invoice
  - חשבונית מס קבלה / Tax Invoice-Receipt, Invoice Receipt
  - קבלה / Receipt
  - חשבונית מס זיכוי, חשבונית זיכוי / Credit Note, Credit Invoice
  - חשבון עסקה, דרישת תשלום / Proforma, Payment Request
  - הצעת מחיר / Quote, Quotation
  - הזמנת עבודה / Work Order

  Copy markers (מקור, העתק, נאמן למקור / Original, Copy) don't change the kind. A bare English "Invoice" with no tax wording is `unknown`, not a guess.
- **When it runs:** in `_run_pipeline`, after extraction, only when `doc_type == 'issued_invoice'`, and on every reprocess. If `raw_extracted_text` is empty, the kind is `unknown` and no call is made.
- **When the call fails:** the document still processes, the kind stays NULL, and the error is logged at ERROR. A NULL kind shows on the Taxes tab as "not classified", so the failure is visible there.
- **Cost:** the call's tokens add to the document's `processing_tokens_*` and `processing_cost_usd`.
- **Editing:** `issued_kind` is editable. Add it to `DocumentUpdate` and to the metadata form, as a select shown for issued documents. A reprocess overwrites it, as it does every other field.
- **Backfill:** a one-off script, `scripts/classify_issued.py`, runs only the classification call over the existing issued documents. It skips already-classified ones unless given `--all` and prints the kind counts. It doesn't use `batch-reprocess`, which would re-run the full extraction and could move dates (#63).

Migration `010` adds the two columns. It also corrects the description of the seeded "Transaction Invoice" category, but only where it still has the seeded text: a חשבון עסקה is a demand for payment and not a tax document, not an "invoice without VAT for exempt transactions". The extraction prompt reads that description.

### Computing the estimate (Stage 2)
`backend/taxes.py` holds pure functions with no I/O, so they can be tested without a database:
- `payment_view(month, vat_frequency)` returns the income-tax basis month, the accruing month, and the VAT period with its `scheduled` flag.
- `summarize(docs, rate)` takes the issued documents in a date range and returns the sums, the counted documents, the copies and the attention list:
  - **Counted kinds:** `tax_invoice` and `tax_invoice_receipt` are +1, `credit_note` is −1, using absolute amounts so a credit is negative whatever sign was extracted.
  - **Amounts:** pre-VAT = `subtotal`, else `total_amount − tax_amount`; VAT = `tax_amount`, else `total_amount − subtotal`. Both convert with the document's `conversion_rate` from #72.
  - **Copies:** documents sharing `(issued_kind, normalized vendor_receipt_id)` count once, the lowest id. If the copies disagree on amount or date, the group goes on the attention list.
  - **Attention list:** issued documents with a NULL or `unknown` kind; counted documents missing a number, an amount, VAT or a conversion; conflicting copies.

`GET /api/stats/taxes?month=YYYY-MM` in `backend/api/stats.py` reads the settings and the issued, non-deleted, `processed` or `needs_review` documents in the dates it needs, then calls the two functions. Response:

```json
{
  "month": "2026-10",
  "settings": {"income_tax_rate": 45.0, "vat_frequency": "bimonthly"},
  "income_tax": {"from": "2026-09-01", "to": "2026-09-30", "base": 0.0, "amount": 0.0, "left_out": 0, "documents": []},
  "income_tax_accruing": {"from": "2026-10-01", "to": "2026-10-31", "base": 0.0, "amount": 0.0, "left_out": 0},
  "vat": {"scheduled": false, "from": "2026-09-01", "to": "2026-10-31", "cap": 0.0, "left_out": 0, "documents": []},
  "attention": [{"document_id": 0, "reason": "missing tax_amount"}]
}
```

- **`documents`:** each entry is `{id, kind, number, date, client_name, pre_vat_ils, vat_ils, counted, copy_of, related}`, where `related` holds the ids found through `referenced_numbers`, either way round.
- **Rate not set:** `amount` is null.
- **Reference currency:** amounts are ILS. If `reference_currency` isn't ILS, the tab says so.

Settings get two `DEFAULTS` entries: `income_tax_rate` (a percent, default `null`) and `vat_frequency` (`"bimonthly"` or `"monthly"`, default `"bimonthly"`).

### The tab (Stage 2)
- **Tabs:** `DashboardPage.tsx` wraps the "Expense Distribution" panel in `components/ui/tabs` as [Taxes | Expense Distribution], with Taxes the default. The time-frame presets stay on the Expense Distribution tab, since they don't apply to Taxes.
- **Month stepper:** ◀ October 2026 ▶.
- **Two lines:**
  - **Income tax:** "₪X: 45% of ₪Y income in September", plus "accruing for November: ₪Z so far".
  - **VAT:** "up to ₪X for Jul–Aug". Outside a payment month it says "No scheduled VAT payment this month · Sep–Oct so far ₪X".
- **Drill-down:** each line opens its documents, grouped by `related`, with copies marked.
- **Attention badge:** "N documents need a look" links each one to its document page.
- **Rate unset:** the income-tax line says "Set the rate in Settings".
- **Settings:** a "Taxes" card on the Settings page holds the rate and the frequency.

## Stages

### Stage 0: data survey (Receiptory's agent, read-only, no PR)
Run these on the live database and record the results in a **Survey** section of this plan.
1. Issued documents by category, `language` and `currency`.
2. `document_title` counts for issued documents, which show the kinds actually in use.
3. `vendor_receipt_id` values shared by more than one issued document, with their categories, dates and totals. These are the copies.
4. Issued documents with NULL `subtotal`, `tax_amount`, `receipt_date` or `vendor_receipt_id`.
5. Issued documents whose `receipt_date` is more than 60 days from `submission_date`. Those that arrived by email are likely misdated (#63).
6. Receipts (category "Receipt (Issued)") and how often a matching tax invoice exists, by number or reference text in `raw_extracted_text`.
7. One ordinary sequence (demand → tax invoice-receipt) and one awkward one (partial payment, credit note or cancellation), by document id.

**What it decides:**
- (a) **Orphan receipts:** if item 6 shows receipts without a tax invoice, add "receipt with no tax invoice" to the attention list in Stage 2.
- (b) **Misdated documents:** if item 5 shows them, the tab adds an attention reason "date far from arrival".
- (c) **Prompt wording:** item 2 may add markers to the classification prompt.

Anything else that contradicts a decision above goes back to the owner.

### Stage 1: issued-document kind (one PR)
- **WP1:** the classification call and the pipeline hook. Owns `backend/processing/issued_kind.py`, the hook in `backend/processing/pipeline.py`, `migrations/010_issued_kind.sql`, and `tests/test_issued_kind.py` plus the additions to `tests/test_pipeline.py`.
- **WP2:** editing and the backfill. Owns `backend/models.py` (`DocumentUpdate`, `DocumentResponse`), `backend/api/documents.py`, `frontend/src/components/MetadataForm.tsx`, and `scripts/classify_issued.py` with its test.
- **Interface:** the two columns and the `issued_kind` enum (one tuple, `ISSUED_KINDS`, in `issued_kind.py`, imported by WP2).

**Verification**
- **Tests, with a fake LLM:**
  - Only issued documents get the call.
  - The enum is enforced, and an off-enum value becomes `unknown`.
  - Empty text gives `unknown` with no call.
  - A failed call leaves the kind NULL and the document processed, with an ERROR logged.
  - A reprocess re-classifies.
  - Tokens are added to the document's cost.
  - PATCH accepts `issued_kind` and rejects an off-enum value.
  - The backfill is idempotent.
  - The migration updates only the unchanged seeded description.
- **Live:** run the backfill. The owner spot-checks 20 random issued documents, Hebrew and English, against the PDFs. Record the kind counts and the cost in this plan; the expected cost is under $1 for a few hundred text-only calls.

### Stage 2: the estimate and the tab (one PR, after #72 merges)
- **WP1:** the backend. Owns `backend/taxes.py`, the `/stats/taxes` endpoint in `backend/api/stats.py`, the `DEFAULTS` in `backend/config.py`, `backend/api/settings.py` if it needs anything, and `tests/test_taxes.py` plus additions to `tests/test_stats.py`.
- **WP2:** the frontend. Owns `frontend/src/pages/DashboardPage.tsx`, a new `frontend/src/components/TaxesPanel.tsx`, `frontend/src/lib/tax-months.ts` (month stepping and labels) with its vitest file, and the Taxes card in `frontend/src/pages/SettingsPage.tsx`.
- **Interface:** the JSON response above.

**Verification**
- **`payment_view` tests:**
  - Bi-monthly: Jan pays for Nov–Dec of the previous year, Mar pays for Jan–Feb, and Oct is unscheduled with Sep–Oct running.
  - Monthly: each month pays for the one before.
- **`summarize` tests:**
  - A demand, a receipt, a quote and a work order count zero.
  - A tax invoice-receipt counts once.
  - Two copies with one number count once, and conflicting copies are flagged.
  - A credit note subtracts whatever sign it was extracted with.
  - A stated VAT of 0 counts as 0.
  - Missing VAT is left out and flagged, never summed as 0.
  - A USD document uses its `conversion_rate`, and an unconverted one is flagged.
  - Related documents link either way round.
- **Endpoint test:** a seeded database, and a null amount when the rate is unset.
- **vitest:** month stepping across a year boundary, and the labels.
- **Live, by eye:** the owner compares the tab for two or three past months with what was actually paid. Expect the right range, not the exact figure: withholding at source and rate changes explain the gaps.

## Open questions
- **Tax withheld at source** reduces the income-tax payment. If it matters in practice, a later change can add a manual monthly adjustment.
- **Bilingual documents:** if invoice4u prints the Hebrew and English versions as one document, the copy rule has nothing to do. Stage 0 item 3 shows which.
