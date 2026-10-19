# Issue #72: convert amounts with cached Bank of Israel rates

**Status:** plan, eng-reviewed 2026-10-18 (decisions below are approved; ledger at the end) · **Branch:** `issue/72-currency-conversion` · **Blocks:** #73 (Taxes tab, Stage 2)

## Goal

Every document with an amount carries the rate that turns its currency into `reference_currency` (default `ILS`) on its `receipt_date`. The dashboard, the export and the documents table use the converted amounts. #73 reads `conversion_rate` and multiplies `subtotal` and `tax_amount` by it.

## The contract #73 depends on (pinned in the #72 comment)

1. `conversion_rate` converts the document's currency into `reference_currency`, at `receipt_date`, or at the last rate published before it. It is per **one unit** of the document's currency, with `UNIT_MULT` already applied.
2. It is exactly `1.0` when the document is already in the reference currency, with no fetch.
3. It is `NULL` when the rate isn't known: no currency, no date, an unsupported currency, or the source unreachable. Never `0`, never a guess.
4. It is recomputed whenever the currency or date changes, by an edit or by a reprocess.

These four points are acceptance tests.

## What exists today

- `documents.converted_amount REAL` and `documents.conversion_rate REAL` exist (migration 001) and are in `DocumentResponse`. Nothing writes them.
- `GET /stats/dashboard` sums raw `total_amount` across currencies (`backend/api/stats.py:43-51`).
- `reference_currency` setting, default `ILS` (`backend/config.py:15`). Free text in Settings, no validation.
- `httpx` is a runtime dependency. It's used sync in `backup/cloud_auth.py` and async in `ingestion/url_fetcher.py`.
- The pipeline is sync and runs in the queue's thread pool, so a blocking HTTP call there is fine. It already makes litellm calls there.
- The latest migration is 009.

**Live data (read-only survey, 2026-10-18, 312 documents not deleted):**

| Currency | Documents | Without `receipt_date` |
|---|---|---|
| ILS | 274 | 7 |
| USD | 19 | 0 |
| PLN | 12 | 0 |
| CZK | 4 | 0 |
| (none) | 2 | 2 |
| GBP | 1 | 0 |

## The source

**Bank of Israel representative rates (שער יציג).** Free, no key:
`https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0/RER_<CCY>_ILS?startperiod=<d>&endperiod=<d>&format=csv`

- The CSV columns are `TIME_PERIOD`, `OBS_VALUE` and `UNIT_MULT`. The rate per unit is `OBS_VALUE / 10**UNIT_MULT`: JPY's `UNIT_MULT` is 2, LBP's is 1.
- 14 series: USD, EUR, GBP, CHF, JPY, CAD, AUD, SEK, NOK, DKK, ZAR, JOD, EGP and LBP. I checked the full list live.
- There are no rows for weekends or holidays.

**Gap: PLN and CZK are not published by the Bank of Israel.** That's 16 of the 36 foreign-currency documents. The European Central Bank publishes about 30 currencies against EUR, PLN and CZK included, also free with no key. I checked it live:
`https://data-api.ecb.europa.eu/service/data/EXR/D.<CCY>.EUR.SP00.A?startPeriod=<d>&endPeriod=<d>&format=csvdata`. On 2026-10-15 it gave PLN 4.37 per EUR.

A cross rate gives PLN→ILS = (EUR→ILS from the BoI) ÷ (PLN per EUR from the ECB). **Owner decision needed (D1).**

## Decisions (approved, see the decision ledger)

- **D1. Currencies the BoI doesn't publish get an ECB cross rate** (R1/D2). CCY→ILS = (EUR→ILS from the BoI) ÷ (CCY per EUR from the ECB), same date rule, cached with source `boi+ecb`.
- **D2. The cache stores published rates only.** `exchange_rates(currency, rate_date, rate_to_ils, source, fetched_at)`, primary key `(currency, rate_date)`. Rows never expire and nothing caches a "missing".
  - Lookup for date D: an exact cached row answers it. Otherwise fetch D−14..D in one call, upsert every row, and use the latest row on or before D.
  - **Any D takes the latest published rate on or before it, today included.** A D after today is clamped to today, so a misdated future receipt gets today's latest rate and is never re-requested (R5/D6: estimates, not precision).
  - A weekend date costs one fetch per conversion. Conversions happen only on processing, edit or the rates task, never on reads.
- **D3. The document stores what was used:**
  - `conversion_rate`: the contract.
  - `converted_amount`: round(`total_amount` × rate, 2), or NULL.
  - New `conversion_rate_date`: the publication date of the rate used.
  - New `conversion_ref`: the reference currency the rate converts into (R3/D4).
  - `subtotal` and `tax_amount` are not stored converted; readers multiply by the rate.
- **D4. One conversion function, `apply_conversion(doc_id)`, with a fixed write protocol** (R2/D3):
  1. Read a snapshot (currency, receipt_date, total_amount, current reference) and close the connection.
  2. Resolve the rate with no connection open (10s HTTP timeout).
  3. Write in a new short transaction with `WHERE id = ? AND currency IS ? AND receipt_date IS ?`. Zero rows means superseded, so skip.

  Callers:
  - (a) The pipeline, after its extraction UPDATE commits.
  - (b) `PATCH /documents/{id}`, after its commit. It refetches only if `currency` or `receipt_date` changed against the stored values, since the form re-sends every field on each save. An amount-only change recomputes `converted_amount` from the stored rate.
  - (c) **The rates task**: its own asyncio task in the `main.py` lifespan, like the backup scheduler (D1 scope answer). It runs at startup and then **every 3 hours** (R4/D5). It converts documents with a currency but no rate, or whose `conversion_ref` isn't the current reference. The first fetch failure ends the pass; the next pass retries.

  A network failure never fails processing or an edit. It leaves NULL and logs a warning.
- **D5. Cross rates for a non-ILS reference.** X→REF = (X→ILS) ÷ (REF→ILS), both at D. ILS→ILS is 1.
- **D6. Changing `reference_currency`** needs no hook (R3/D4). Every reader uses a rate only when `conversion_ref` equals the current reference, and the rates task recomputes the rest. That covers changes through Settings, `.env` and a restored backup alike.
- **D7. The currency code is normalized before lookup.** Upper-case it and strip it. Map `NIS`, `₪` and `ש"ח` to `ILS`, `$` to `USD`, `€` to `EUR` and `£` to `GBP`. Anything that isn't a 3-letter code is unsupported and gets NULL. The stored `currency` is not rewritten. `reference_currency` goes through the same normalization, so an invalid one simply yields NULL rates (R6, no change).
- **D8. Readers.** Each uses `converted_amount` / `conversion_rate` only where `conversion_ref` equals the current reference.
  - **Dashboard:** category totals sum `converted_amount`. The response adds `reference_currency` and `unconverted_count`, and the page labels the totals and shows the count.
  - **Export:** CSV and Excel add `converted_amount`, `conversion_rate`, `conversion_rate_date` and `conversion_ref`.
  - **Documents table:** when the currency differs from the reference, show the converted amount under the original.
  - **Detail page:** a read-only line, "≈ ₪1,234.50 at 3.103 (2026-10-15)", or "No rate yet" when there is no rate.

**Contract correction (review):** contract (2) gives 1.0, with no fetch, to any document already in the reference currency, dated or not. "No date gives NULL" applies only to a foreign-currency document.

## Tasks

- [ ] **T1 migration 010.** Create the `exchange_rates` table. Add `documents.conversion_rate_date TEXT` and `documents.conversion_ref TEXT`. No index: the rates-task query scans about 400 rows.
- [ ] **T2 `backend/currency.py`.**
  - `normalize_code`.
  - `fetch_boi(ccy, start, end)` and `fetch_ecb(ccy, start, end)`. Both use sync httpx with a 10s timeout, parse the CSV and apply `UNIT_MULT`; the ECB values are CCY per EUR.
  - `rate_to_ils(ccy, date)`: cache, then fetch, then the latest row on or before the date, with the date clamped to today; BoI first, ECB cross rate otherwise.
  - `rate(ccy, ref, date)`.
  - `apply_conversion(doc_id)`, using the D4 protocol.
  - `convert_pending(limit=None)` for the rates task.
- [ ] **T3 pipeline:** call `apply_conversion` after the extraction UPDATE commits.
- [ ] **T4 PATCH:** after commit, refetch on a real currency or date change; an amount-only change recomputes `converted_amount` from the stored rate. The conversion columns stay out of `edit_history`.
- [ ] **T5 rates task** `run_rates_task` in the `main.py` lifespan (startup, then every 3h; the first failure ends the pass), only when `run_background` is on.
- [ ] **T6 readers:** stats (`reference_currency` and `unconverted_count`), export columns, `DocumentTable`, `DashboardPage` labels, detail-page line.
- [ ] **T7 docs:** CLAUDE.md (rates source, the contract including `conversion_ref`, that the network is used for rates, the 3h task), the spec, README, TODOS. Add a comment on #72/#73 that #73 must also check `conversion_ref = 'ILS'`.

## Tests

- **Parse:** a USD CSV; JPY with `UNIT_MULT` 2 (1.9446 → 0.019446); LBP with `UNIT_MULT` 1; an ECB PLN CSV; an empty CSV.
- **Lookup:**
  - An exact hit makes no HTTP call.
  - A weekend date resolves to Friday.
  - A future date resolves to the latest rate on or before today.
  - A date before the first published rate gives `None`.
  - An HTTP error or timeout gives `None`, logged.
  - Rows from one fetch serve a later lookup without HTTP.
  - PLN resolves through BoI EUR ÷ ECB PLN.
- **Contract:**
  - (1) A USD document's rate equals the BoI value, per unit.
  - (2) An ILS document gets 1.0 and makes no HTTP call, even without a date.
  - (3) No currency, no foreign date, an unsupported code, or an unreachable source gives NULL, never 0.
  - (4) Editing the currency or the date recomputes, and a reprocess recomputes.
- **Write protocol:** a currency edit between the snapshot and the write keeps the new currency's rate. No connection is open during the fetch.
- **Reference:** a reference change through env makes stored rates unusable to readers, and the next pass recomputes them. A cross rate works for a non-ILS reference.
- **Rates task:** a fetch failure ends the pass; documents still needing a rate are picked up by the next pass.
- **Pipeline:** a network failure still leaves the document `processed`, with a NULL rate.
- **Stats** sum converted amounts and report `unconverted_count`. **Export** columns.
- No test touches the network. Fetchers are monkeypatched at `backend.currency`.

## Eng review (/plan-eng-review, 2026-10-18)

**Target:** this plan, `docs/plans/2026-10-18-issue72-currency-conversion.md` (fixed). Report file: this file.

### Scope record

feature answers: no cuts proposed; structure: A "Original arrangement" (D1, owner, 2026-10-18); accepted scope: backend/currency.py + migration 010, a separate rates task in the main.py lifespan (like the backup scheduler), pipeline/documents/settings APIs call currency.py, readers in stats, export, DocumentTable, DashboardPage and the detail page; pending remedies: R1 ECB fallback, R2 conversion write protocol, R3 stale-reference detection, R4 retry cadence, R5 future-dated receipts, R6 reference-currency validation.

Scope Challenge result: scope accepted as-is.

## Decision ledger

### R1: currencies the Bank of Israel does not publish (plan D1)
Finding: 1, P1, confidence 10/10, plan "The source" section; live probe of the BoI EXR list (no PLN/CZK) and ECB EXR (PLN 4.37/EUR on 2026-10-15); reviewer: plan-eng-review
Plan baseline: D1 left open for the owner (original proposal: A ECB cross rate, or B NULL)
Runtime evidence: live DB 2026-10-18: 12 PLN + 4 CZK documents of 36 foreign (measured); BoI has 14 RER series, none PLN/CZK (probe)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1 non-BoI currencies | pending | ECB cross rate via EUR (EUR→ILS from BoI ÷ CCY per EUR from ECB), source `boi+ecb` | NULL rate, BoI only |
| R2–R6 | pending | pending | pending |
Question D2:
D2 — Convert PLN, CZK and other currencies the Bank of Israel doesn't publish?
Project/branch/task: issue/72-currency-conversion, plan D1.
ELI10: The Bank of Israel publishes daily shekel rates for 14 currencies, but not Polish zloty or Czech koruna, and 16 of your 36 foreign-currency receipts are in those two. The European Central Bank publishes about 30 currencies against the euro, free and without a key, so a zloty amount can go zloty to euro (ECB) and then euro to shekel (Bank of Israel). Without that, those receipts keep no rate and stay out of converted totals.
Stakes if we pick wrong: B leaves 16 receipts (and any future ones in those currencies) out of every converted total and out of #73's tax estimate.
Recommendation: A because it converts every receipt you have today, using two official central-bank sources, for one more small fetcher.
Completeness: A=10/10, B=6/10
Pros / cons:
A) ECB cross rate (recommended)
  ✅ All 36 foreign-currency receipts get a rate today, including the 12 PLN and 4 CZK ones
  ✅ Both sources are official central-bank reference rates, free with no API key, stored with source boi+ecb
  ❌ A second outside source to fetch and test; a cross rate can differ from a direct BoI rate by a fraction of a percent
B) Bank of Israel only
  ✅ One source, the one you named; fewer moving parts and nothing to explain about cross rates
  ❌ PLN and CZK receipts stay unconverted, shown as "No rate", and are left out of dashboard totals and #73
Net: one extra fetcher buys conversion for the 16 receipts the Bank of Israel can't price.
Header: Non-BoI FX
Options:
A) ECB cross rate (Recommended)
For a currency the BoI doesn't publish, fetch its rate per EUR from the ECB and divide BoI's EUR→ILS by it, same date rule; cache rows with source boi+ecb. human: ~3h / CC: ~15min.
B) Bank of Israel only
Unsupported currencies get conversion_rate NULL and show "No rate"; no second source.

State: approved
Actual answer: A "ECB cross rate (Recommended)" (D2, owner, 2026-10-18)
Accepted scope: for a currency the BoI doesn't publish, fetch its rate per EUR from the ECB and divide BoI's EUR→ILS by it, same date rule; cache rows with source boi+ecb; fetcher, parsing and cross-rate tests.
History: none

### R2: conversion write protocol (no network inside a write transaction; compare-and-set)
Finding: 2, P1, confidence 9/10, plan D4 `apply_conversion(conn, doc_id)` with backend/database.py:41-46 (`sqlite3.connect(path, timeout=10)` … `yield conn` … `conn.commit()`); reviewer: plan-eng-review, Section 1
Plan baseline: D4 original proposal: one `apply_conversion(conn, doc_id)` called inside the caller's connection (pipeline after its UPDATE, PATCH, backfill)
Runtime evidence: get_connection commits only on exit; sqlite3 holds the write lock from the first UPDATE to commit; other writers wait timeout=10s then raise "database is locked" (static read, database.py:41-46)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R2 write protocol | apply_conversion(conn, doc_id) inside the caller's transaction | `apply_conversion(doc_id)`: read a snapshot (currency, receipt_date, total_amount) and close; resolve the rate with no connection open (10s HTTP timeout); write in a new short transaction with `WHERE id=? AND currency IS ? AND receipt_date IS ?`; 0 rows = superseded, skip; callers invoke it only after their own commit | as proposed: fetch inside the caller's transaction, last writer wins |
| R1 non-BoI FX | approved A (D2) | ECB cross rate | ECB cross rate |
| R3–R6 | pending | pending | pending |
Question D3:
D3 — How should a conversion write its rate without blocking the database or overwriting a newer edit?
Project/branch/task: issue/72-currency-conversion, plan D4.
ELI10: Fetching a rate can take seconds. In the plan, that fetch happens while the database is locked for writing, so a receipt finishing processing at the same moment waits and then fails with "database is locked". Separately, three things can convert the same receipt (processing, your edit, the background pass), and a slow one could write the rate for the old currency after you changed it. The fix: look up the receipt, fetch the rate with nothing locked, then save only if the currency and date are still what was looked up.
Stakes if we pick wrong: B can fail a receipt's processing with "database is locked" whenever a rate fetch is slow, and can leave a wrong rate after an edit.
Recommendation: A because it removes both failure modes with a few lines and needs no locking beyond what SQLite already does.
Completeness: A=10/10, B=4/10
Pros / cons:
A) Fetch unlocked, then compare-and-set (recommended)
  ✅ A slow or hung rates site never holds SQLite's write lock, so processing and edits never hit "database is locked"
  ✅ A stale conversion that lost a race writes nothing; the newer edit's own conversion stands
  ❌ The save is two short transactions instead of one, and callers must convert after committing their own write
B) As proposed (inside the caller's transaction)
  ✅ One transaction per path; the rate lands atomically with the edit or the extraction write
  ❌ Any fetch slower than SQLite's 10s busy timeout makes a concurrent writer fail; races can leave the old currency's rate
Net: A trades one extra tiny transaction for never locking the database across a network call.
Header: Write protocol
Options:
A) Fetch unlocked, then CAS (Recommended)
apply_conversion(doc_id) reads a snapshot, fetches with no connection open (10s timeout), then UPDATE ... WHERE id AND currency IS ? AND receipt_date IS ?; 0 rows means superseded and is skipped. Pipeline and PATCH call it after their own commit. Tests: a concurrent currency edit between snapshot and write keeps the new currency's rate; no connection is open during the fetch. human: ~2h / CC: ~10min.
B) As proposed
apply_conversion(conn, doc_id) inside the caller's transaction; last writer wins.

State: approved
Actual answer: A "Fetch unlocked, then CAS (Recommended)" (D3, owner, 2026-10-18)
Accepted scope: apply_conversion(doc_id) reads a snapshot, fetches with no connection open (10s timeout), then UPDATE ... WHERE id AND currency IS ? AND receipt_date IS ?; 0 rows = superseded, skipped. Pipeline and PATCH call it after their own commit. Tests: a concurrent currency edit between snapshot and write keeps the new currency's rate; no connection is open during the fetch.
History: none

### R3: detecting rates computed for an old reference currency (plan D6)
Finding: 3, P2, confidence 9/10, plan D6 (settings PATCH hook, backend/api/settings.py:108-118 `set_setting(key, value)`) vs backend/config.py:238-245 (`env_val = os.environ.get(env_key)` wins over the db); reviewer: plan-eng-review, Section 1
Plan baseline: D6 original proposal: a reference_currency change through PATCH /settings sets every rate NULL and wakes the backfill
Runtime evidence: get_setting precedence is env > db > default (static read config.py:238-256); a .env change plus restart never calls patch_settings
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R3 stale-reference detection | settings PATCH hook nulls all rates | new `documents.conversion_ref TEXT` = reference the rate was computed for; the rates task recomputes rows whose conversion_ref differs from the current reference; readers use a rate only when conversion_ref equals the current reference (else treat as NULL); no settings.py hook | as proposed: PATCH hook only |
| R2 write protocol | approved A (D3) | CAS also checks conversion_ref snapshot | CAS as approved |
| R1 | approved A (D2) | ECB cross rate | ECB cross rate |
| R4–R6 | pending | pending | pending |
Question D4:
D4 — How should the app notice rates computed for an old reference currency?
Project/branch/task: issue/72-currency-conversion, plan D6.
ELI10: Rates are stored per receipt, for whatever the reference currency was at the time. If the reference changes, every stored rate is wrong. The plan only catches a change made in Settings, but this app lets a .env variable override that setting, and a .env change plus restart goes around Settings entirely. Recording on each receipt which reference its rate was for catches every kind of change, and readers can ignore a mismatched rate right away.
Stakes if we pick wrong: with B, a reference change through .env leaves every converted total silently in the old currency.
Recommendation: A because it catches the change whichever way it happens, and readers never show a mismatched rate even before the recompute finishes.
Completeness: A=10/10, B=6/10
Pros / cons:
A) Store the reference per receipt (recommended)
  ✅ Catches Settings edits, .env overrides and a restored backup alike, with no hook in the settings API
  ✅ Readers drop a mismatched rate immediately, so a total is never mixed across two reference currencies
  ❌ One more column in migration 010, and every reader adds `conversion_ref = reference` to its condition
B) Settings hook only (as proposed)
  ✅ No extra column; the settings PATCH clears all rates the moment the setting is saved
  ❌ A .env change of RECEIPTORY_REFERENCE_CURRENCY plus restart leaves every rate in the old currency, silently
Net: one column turns a Settings-only safeguard into one that holds however the reference changes.
Header: Stale reference
Options:
A) Store reference per receipt (Recommended)
Migration 010 adds documents.conversion_ref; apply_conversion writes it with the rate; the rates task recomputes rows where conversion_ref IS NOT the current reference; stats/export/UI and #73 use the rate only when conversion_ref matches; settings.py unchanged. Tests: a reference change via env makes rates unusable until recomputed, then recomputes them. human: ~1h / CC: ~5min.
B) Settings hook only
PATCH /settings with a new reference_currency sets every rate NULL; env changes are not detected.

State: approved
Actual answer: A "Store reference per receipt (Recommended)" (D4, owner, 2026-10-18)
Accepted scope: migration 010 adds documents.conversion_ref; apply_conversion writes it with the rate (and its CAS includes it); the rates task recomputes rows where conversion_ref IS NOT the current reference; stats/export/UI and #73 use the rate only when conversion_ref matches; settings.py unchanged (plan D6 hook dropped). Tests: a reference change via env makes rates unusable until recomputed, then recomputes them.
History: none

### R4: rates task cadence and backoff (plan D4c)
Finding: 4, P2, confidence 8/10, plan D4(c) "at startup and then every 6 hours"; reviewer: plan-eng-review, Section 1
Plan baseline: D4(c) original proposal: startup + every 6h, no backoff, no per-pass bound
Runtime evidence: unknown (proposed code). Scale [measured 2026-10-18]: 36 foreign documents, 2 without currency; one BoI range fetch covers 15 days per currency
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R4 cadence | startup + every 6h, no backoff | startup, then every 10 min; each pass picks at most 50 documents needing a rate (indexed query); the first fetch failure ends the pass and the next pass waits 1h | as proposed |
| R3 stale ref | approved A (D4) | recompute rows with a mismatched conversion_ref | same |
| R2, R1 | approved | unchanged | unchanged |
| R5, R6 | pending | pending | pending |
Question D5:
D5 — How often should the rates task run, and what if the rate sites are down?
Project/branch/task: issue/72-currency-conversion, plan D4(c).
ELI10: The background task fills in missing rates: old receipts, rates the bank hadn't published yet, receipts whose fetch failed, and every receipt after a reference-currency change. Every 6 hours means a receipt can show "No rate yet" for most of a day. Checking every 10 minutes costs one small database query when there's nothing to do. If the bank's site is down, the task should stop that pass and wait an hour, not hammer it.
Stakes if we pick wrong: B leaves new foreign receipts and a reference change unconverted for up to 6 hours, with no limit on retries during an outage.
Recommendation: A because rates appear within minutes and an outage costs one failed request per hour.
Completeness: A=10/10, B=7/10
Pros / cons:
A) Every 10 min, with backoff (recommended)
  ✅ A rate the bank publishes later in the day, or a reference change, shows up within about 10 minutes
  ✅ An outage costs one failed request per hour; a pass never takes more than 50 documents
  ❌ A tiny indexed query runs every 10 minutes even when nothing needs converting
B) Startup and every 6 hours (as proposed)
  ✅ Fewer wake-ups; the simplest loop to read
  ❌ Up to 6 hours of "No rate yet", and nothing limits retries while a rates site is down
Net: a cheap 10-minute check plus an hour of backoff beats a 6-hour wait with unbounded retries.
Header: Rates cadence
Options:
A) Every 10 min, backoff (Recommended)
The rates task runs at startup, then every 10 minutes; a pass takes at most 50 documents needing a rate; the first fetch failure ends the pass and the next one waits 1 hour. Tests: a failure stops the pass and sets the 1h wait; the 50-document bound. human: ~1h / CC: ~5min.
B) Every 6 hours
Startup plus every 6 hours, no backoff and no per-pass bound.

State: approved
Actual answer: free-text "3 hours" (D5, owner, 2026-10-18), neither listed option
Accepted scope: the rates task runs at startup, then every 3 hours; the first fetch failure ends the pass (the next pass is the retry, 3h later); no per-pass document bound (at a 3h cadence a 50-document bound would stretch a reference-change recompute of ~312 documents over ~a day). Interpretation stated to the owner in chat. Tests: a fetch failure ends the pass; documents needing a rate are picked up by the next pass.
History: none

### R5: dates on or after today (plan D2)
Finding: 5, P2, confidence 9/10, plan D2 "fetch D−14..D … use the latest row on or before D"; prior learning confidence-blind-to-date-misreads (28 of 251 dated docs misdated, incl. wrong years); reviewer: plan-eng-review, Section 1
Plan baseline: D2 original proposal: fetch D−14..D and take the latest row ≤ D, for any D
Runtime evidence: BoI publishes each business day's rate in the afternoon (Israel time); no rows for weekends/holidays (probe 2026-10-08..16: 10-10 and 10-11 absent)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R5 date rules | any D: latest row ≤ D | D after today (Asia/Jerusalem): NULL, no fetch; D == today with no row for D yet: NULL, retried by the next pass; the "latest row before D" fallback only for D before today | as proposed |
| R1–R4 | approved | unchanged | unchanged |
| R6 | pending | pending | pending |
Question D6:
D6 — How should receipts dated today or in the future get their rate?
Project/branch/task: issue/72-currency-conversion, plan D2.
ELI10: The Bank of Israel publishes today's rate in the afternoon. A receipt processed this morning would, under the plan, take yesterday's rate as "the last one published" and keep it forever, though today's rate appears a few hours later. And a receipt whose year the model misread as 2027 can never get a rate, so every pass would ask the bank again. The fix: a future date gets no rate and no request; today's date waits until today's rate exists (or tomorrow, on a weekend); only past dates fall back to the last published rate.
Stakes if we pick wrong: B stores yesterday's rate on same-day receipts permanently and re-requests misdated future receipts every pass.
Recommendation: A because it gives same-day receipts the right rate and never asks the bank about dates it can't have.
Completeness: A=10/10, B=5/10
Pros / cons:
A) Today waits, future skipped (recommended)
  ✅ A receipt dated today gets today's published rate, not yesterday's, once the bank publishes it
  ✅ Misdated future receipts cost no requests and show "No rate yet" until their date is fixed
  ❌ A same-day receipt shows "No rate yet" until the afternoon publication (until Monday on a weekend)
B) Latest published rate for any date (as proposed)
  ✅ Every receipt gets some rate immediately, even one dated today or in the future
  ❌ Same-day receipts keep yesterday's rate forever, and future-dated ones are re-requested every pass
Net: a few hours of "No rate yet" on same-day receipts buys the correct rate and no wasted requests.
Header: Date rules
Options:
A) Today waits, future skipped (Recommended)
receipt_date after today (Asia/Jerusalem): NULL, no fetch. receipt_date == today with no row for that date: NULL, the next pass retries. The latest-row-before fallback applies only to dates before today. Tests: future date makes no HTTP call; today without a row stays NULL and is not filled with yesterday's rate; yesterday-weekend resolves to Friday. human: ~1h / CC: ~5min.
B) Latest published rate for any date
Any receipt_date takes the latest row on or before it, including today and future dates.

State: approved
Actual answer: B, as free text: "Latest is fine. Again, we are estimating here. I don't care about precision and ok with approximation. I need to know order of magnitude - how much taxes i'm expected to pay, not to the dollar" (D6, owner, 2026-10-18)
Accepted scope: any receipt_date takes the latest published row on or before it, today included. Applying the owner's stated principle (approximation is fine), a receipt_date after today is clamped to today, so a misdated future receipt gets today's latest rate instead of being re-requested every pass; stated to the owner in chat. Tests: a weekend date resolves to Friday; a future date resolves to the latest rate on or before today with one fetch.
History: none

### R6: an invalid reference_currency setting (withdrawn, no change)
Finding: 6, P3, confidence 7/10, backend/api/settings.py:108-118 writes any value for a DEFAULTS key, so reference_currency can be "usd" or "XYZ"; reviewer: plan-eng-review, Section 1
Plan baseline: no validation proposed
Runtime evidence: static read
Disposition: withdrawn by the reviewer without a question. The setting is normalized with the same normalize_code as document currencies ("usd" works), and an unsupported reference yields NULL rates under contract (3), which every reader shows as "No rate". Given the owner's estimates-not-precision principle (D6), validation in Settings is not worth a decision. No accepted scope.
State: approved (no change)
Actual answer: none needed (no remedy proposed)
Accepted scope: none
History: none

Approval readiness: PASS. R1 (D2), R2 (D3), R3 (D4), R4 (D5, free text "3 hours"), R5 (D6, free text "Latest is fine…"), R6 (withdrawn, no remedy); structure D1.

## Review sections

**Reality rows:**
- `premise-table`: pass, unverified=0.
  - `migrations/001_initial_schema.sql:87-88`: the converted columns exist.
  - `backend/api/stats.py:43-51`: the totals sum raw `total_amount`.
  - `backend/config.py:15`: the `reference_currency` setting.
  - `pyproject.toml:18`: httpx is a dependency.
  - `backend/processing/queue.py:59`: the pipeline runs in the executor.
  - `migrations/`: the latest is 009.
  - BoI and ECB endpoints probed live.
- `already-done`: pass. `git log origin/master --grep currency|exchange|conversion` has no conversion work.
- `surface-check`: pass. Both fetch URLs returned CSV live on 2026-10-18.
- `binding-decisions`: pass.
  - #73's plan D9 and the #72 contract comment are honored. `conversion_ref` adds a reader condition, and T7 reports it on the issue.
  - The owner's "Bank of Israel rates" decision is kept, with the ECB used only for currencies the BoI doesn't publish (D2).
- `numbers`: pass. 312 live documents, 36 foreign, 16 PLN/CZK [measured: live DB, read-only, 2026-10-18].
- `deferred-asks`: pass. Every reader the issue lists is in T6. The Taxes tab is #73.
- `pr-count`: one PR (no ship-policy file; default).

REALITY: premise-table pass 9 premises with receipts, unverified=0 migrations/001_initial_schema.sql:87
REALITY: already-done pass no conversion work on origin/master backend/api/stats.py:43
REALITY: surface-check pass BoI and ECB CSV endpoints probed live 2026-10-18 docs/plans/2026-10-18-issue72-currency-conversion.md:40
REALITY: binding-decisions pass #73 D9 contract honored, BoI kept as primary source docs/plans/2026-10-18-issue73-tax-estimates.md:41
REALITY: numbers pass 312/36/16 measured on live DB read-only docs/plans/2026-10-18-issue72-currency-conversion.md:26
REALITY: deferred-asks pass all issue #72 readers in T5, tax tab is #73 backend/api/export.py:62
REALITY: pr-count pass one PR, no ship-policy file backend/api/stats.py:43

**1. Architecture.** Findings and dispositions:
- **Network fetch inside a write transaction, and the conversion race** (P1, 9/10): resolved by R2, accepted (D3).
- **Env changes to the reference bypass a settings hook** (P2, 9/10): resolved by R3, accepted (D4).
- **6h cadence with no backoff** (P2, 8/10): resolved by R4, accepted as 3h (D5).
- **Future and same-day dates** (P2, 9/10): resolved by R5, latest rate with a clamp to today (D6).
- **Reference validation** (P3, 7/10): R6, withdrawn.

Flow:
```
 pipeline UPDATE ──commit──┐
 PATCH UPDATE ─────commit──┼──► apply_conversion(doc_id)
 rates task (3h) ──────────┘        │ 1. snapshot (currency, receipt_date, total, ref) ─ close
                                     │ 2. rate(ccy, ref, min(D, today))  [no connection open]
                                     │      same ccy → 1.0
                                     │      cache exact row? → hit
                                     │      fetch_boi(D-14..D) → upsert → latest ≤ D
                                     │      not a BoI series → fetch_ecb(EUR, ccy) cross
                                     │      error/timeout → None (warning)
                                     │ 3. UPDATE … WHERE id AND currency IS ? AND receipt_date IS ?
                                     ▼      0 rows → superseded, skip
 readers (stats, export, table, detail, #73): use rate only WHERE conversion_ref = reference
```

**2. Code quality.** No new choices. Notes for the build:
- PATCH must compare against stored values, because `MetadataForm.handleSave` re-sends every field (`frontend/src/components/MetadataForm.tsx:64-71`).
- The conversion columns stay out of `edit_history` (`backend/api/documents.py:215-223`).
- Keep the normalize map small. Live currencies are clean ISO codes.

**3. Tests.** The framework is pytest plus vitest (CLAUDE.md Testing).
```
CODE PATHS                                               USER FLOWS
[+] backend/currency.py                                  [+] Foreign receipt processed
  ├── parse BoI/ECB CSV, UNIT_MULT        [GAP→T2 tests]    ├── [GAP] rate stored, table shows ≈ ₪   (pipeline test)
  ├── rate_to_ils: exact hit / fetch /                       └── [GAP] rates site down → processed, "No rate yet"
  │     weekend / future clamp / none     [GAP]          [+] Owner edits currency or date
  ├── ECB cross for PLN/CZK               [GAP]             └── [GAP] rate recomputed after save (PATCH test)
  ├── rate(): same ccy 1.0 / cross ref    [GAP]          [+] Reference changed in .env
  └── apply_conversion: snapshot/CAS race [GAP]             └── [GAP] old rates ignored, next pass recomputes
[+] rates task: pass, failure ends pass   [GAP]          [+] Dashboard / export
[+] stats unconverted_count, export cols  [GAP]             └── [GAP] totals in reference currency, count shown
[=] stats.py existing tests (tests/test_stats.py:24)  ★★  must change: totals now converted (CRITICAL regression)
COVERAGE: 0/12 new paths tested (all planned)  |  GAPS: 12 (0 E2E, 0 eval)
```
- **Regression (CRITICAL):** `tests/test_stats.py` asserts today's raw sums.
  - Behavior to keep: an ILS-only install shows the same totals, because rate 1.0 times the amount is the amount.
  - Intentional change: foreign amounts are converted, and unconverted ones are excluded and counted.
  - This regression contract follows from the accepted scope (D8). It is not a new policy.
- **Value cards:** every T2 to T6 test has one in the Test Plan artifact.
- **Tests retired:** none.
- **LLM/eval scope:** none. No prompt or extraction change.

**4. Performance.** No new choices.
- The rates task scans about 400 rows every 3h, so no index is needed (T1).
- An initial backfill of about 36 foreign documents costs at most one fetch per (currency, 15-day window). That is under 36 calls, and in practice about 10 [estimated].
- A PATCH that changes the currency or date adds one cached lookup, or one fetch of up to 10s if the site hangs. That is the accepted R2 trade.

## NOT in scope
- **The Taxes tab** is #73. It only reads `conversion_rate` and `conversion_ref`.
- **Fixing misdated receipts** is #63. A wrong year gives a plausible but wrong rate, which is acceptable under the estimates principle.
- **Validating `reference_currency` in Settings** (R6): withdrawn.
- **Storing converted `subtotal` and `tax_amount`:** readers multiply.

## What already exists
- Columns `converted_amount` and `conversion_rate` (migration 001), reused.
- The `reference_currency` setting, reused.
- httpx sync usage (`backend/backup/cloud_auth.py`), the same pattern.
- The backup-scheduler task pattern in the `main.py` lifespan, mirrored for the rates task.
- The CSV/Excel export field list (`backend/api/export.py:62-68`), extended.

## Failure modes
- **Rates site down or slow:** NULL plus a warning. The pass ends and retries in 3h, and the UI shows "No rate yet". Tested (T2/T5), not silent.
- **Concurrent edit during a fetch:** the compare-and-set skips the write. Tested (R2).
- **Reference changed via `.env`:** readers ignore the mismatched rates and the next pass recomputes. Tested (R3).
- **A CSV format change at the BoI:** the parse returns no rows, so NULL plus a warning, the same as an outage. Covered by the empty-CSV parse test.
- **Critical gaps:** 0.

## Worktree parallelization strategy
Sequential implementation, no parallelization opportunity. The readers depend on `currency.py` and migration 010, and the frontend change is small.

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~30min / CC: ~3min)** — migrations — migration 010: the exchange_rates table, conversion_rate_date and conversion_ref
  - Surfaced by: Architecture — R3 (conversion_ref), plan D3
  - Files: migrations/010_currency_conversion.sql
  - Verify: `uv run pytest tests/test_database.py -q`
- [ ] **T2 (P1, human: ~1d / CC: ~30min)** — currency — currency.py: fetchers, cache, rate(), apply_conversion with snapshot/CAS, ECB cross, date clamp
  - Surfaced by: Architecture — R1, R2, R5
  - Files: backend/currency.py, tests/test_currency.py
  - Verify: `uv run pytest tests/test_currency.py -q`
- [ ] **T3 (P1, human: ~1h / CC: ~5min)** — pipeline/api — call apply_conversion after the pipeline and PATCH commits
  - Surfaced by: Architecture — R2
  - Files: backend/processing/pipeline.py, backend/api/documents.py, tests/test_pipeline.py, tests/test_documents_api.py
  - Verify: `uv run pytest tests/test_pipeline.py tests/test_documents_api.py -q`
- [ ] **T4 (P1, human: ~2h / CC: ~10min)** — main — the rates task every 3h; the first failure ends the pass; mismatched conversion_ref is recomputed
  - Surfaced by: Architecture — R3, R4
  - Files: backend/main.py, backend/currency.py, tests/test_currency.py
  - Verify: `uv run pytest tests/test_currency.py -q`
- [ ] **T5 (P1, human: ~3h / CC: ~15min)** — readers — stats, export, DocumentTable, DashboardPage, detail page (conversion_ref = reference)
  - Surfaced by: Test review — the CRITICAL stats regression; plan D8
  - Files: backend/api/stats.py, backend/api/export.py, frontend/src/components/DocumentTable.tsx, frontend/src/pages/DashboardPage.tsx, frontend/src/components/MetadataForm.tsx, tests/test_stats.py, tests/test_export.py
  - Verify: `uv run pytest tests/test_stats.py tests/test_export.py -q && cd frontend && npm run build && npm test`
- [ ] **T6 (P2, human: ~30min / CC: ~5min)** — docs — CLAUDE.md, the spec, README, TODOS; a comment on #73 about conversion_ref
  - Surfaced by: Architecture — R3 (a new reader condition for #73)
  - Files: CLAUDE.md, docs/specs/2026-03-28-receiptory-v1-design.md, README.md, TODOS.md
  - Verify: read

## Unresolved decisions
None.

## Completion summary
- Step 0: Scope Challenge — scope accepted as-is (structure A, D1).
- Reality rows: 7/7 applicable rows emitted; 0 unverified premises.
- Architecture Review: 5 issues found (R2–R6; R1 came from the Scope Challenge).
- Code Quality Review: 0 issues found (2 build notes).
- Test Review: diagram produced; 12 gaps identified, all planned, 1 CRITICAL regression contract (stats).
- Performance Review: 0 issues found.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 0 items proposed.
- Failure modes: 0 critical gaps flagged.
- Unresolved decisions: 0 in this review.
- Outside voice: codex, unavailable (not installed; the native fallback needs TaskOutput, which this host lacks).
- Parallelization: 1 lane, 0 parallel, 1 sequential.
- Lake Score: 3/5. D2, D3 and D4 picked the 10/10 option; D5 and D6 were free-text answers (3h cadence, latest rate).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | codex via `/plan-eng-review` | Independent 2nd opinion | 1 | unavailable | codex not installed; native fallback needs TaskOutput (absent) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN | 17 issues, 0 critical gaps (all mapped to approved tasks T1–T6) |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex, plan-review phase, unavailable (not installed); no native fallback (the host lacks TaskOutput); 0 findings.
- **VERDICT:** no review is CLEAR. Eng review: issues found and mapped to approved tasks, with 0 unresolved decisions; the plan is ready to implement. The "eng review required" gate counts mapped issues as open.

NO UNRESOLVED DECISIONS
