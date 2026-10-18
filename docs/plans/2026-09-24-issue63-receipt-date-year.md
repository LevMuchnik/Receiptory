# Issue #63: misdated receipts (wrong year at high confidence)

**Status:** plan, pre-review · **Branch:** `issue/63-receipt-date-year` · **Builds on:** #67 (schema)

## Problem

31 of 252 dated documents (12%) have the wrong `receipt_date`, every one at confidence
0.85–0.98, so none reached `needs_review`. Every true date was read off the scan by eye
(2026-09-24; `.gstack/issue63-verified-dates.json`, gitignored). **All 31 are 2026
receipts**, including the two insurance policies. None is an old document.

The model gets it wrong in three ways:

| mechanism | docs | example | can a "date as printed" field fix it? |
|---|---|---|---|
| A. `DD/MM/YY` read as `YY/MM/DD` (day becomes the year) | 55 60 62 111 181 315 321 331 | printed `18/04/26`, stored `2018-04-26` | **yes**, the transcription is right (`raw_extracted_text` has `18/04/26`) |
| B. year replaced, day+month right | 15 32 36 41 44 76 84 88 112 119 120 123 195 207 229 252 254 285 292 | printed `26-05-2026`, stored `2024-05-26` | **no**, the model writes `2024` in its own transcription too |
| C. other: form-revision stamp taken as the year (225 `ספטמבר 2021`, 226 `09.24`), digit misread (318 `18`→`15`), US `M/D/YY` print header (26) | 225 226 318 26 | | partly |

B is the big one (19 docs), and a prompt or schema change does not reach it: the model
reads a clearly printed four-digit `2026` as `2024` (twelve docs print the full year).
**The fix therefore has to live in code, anchored on the upload date.**

The anchor is sound on this corpus: excluding the 31, the gap between upload and
`receipt_date` is 0–7 days for 205 documents, 8–90 days for 13, and never more than 90.
Three documents are dated 37–39 days *after* upload, all Mevaseret municipal bills whose
printed date is the payment due date (65, 184, 266). They are correct.

**Already exported:** 25 of the 31 went out in the "since last export" batches of
07-06, 08-02 and 09-03, with the wrong date in the CSV and in the zip filename.

**Also found:** `PATCH /documents/{id}` (the UI edit) updates `receipt_date` but not
`stored_filename`, so a date fixed by hand leaves `filed/` under the old, wrong name.
The owner browses `filed/` directly.

## Design

### 1. `resolve_receipt_date` (new, pure, `backend/processing/dates.py`)

Input: the model's `receipt_date`, its new `receipt_date_as_printed`, and the upload
date (`submission_date`). Output: the date to store plus an optional review reason.

- **Plausible window:** `[upload − 180 days, upload + 1 day]`. The one day absorbs UTC vs
  Israel time at midnight.
- Candidates, first plausible wins:
  1. `receipt_date_as_printed` parsed day-first (`D/M/YY`, `D.M.YYYY`, `D-M-YY`, ...;
     `YYYY-MM-DD` / `YYYY/MM/DD` when the first group has four digits). Fixes A.
  2. the model's `receipt_date` as is (today's behaviour, 218/218 clean docs).
  3. the model's day and month in the upload year, or the year before (for January
     uploads of December receipts). Fixes B.
- Model date **after** the window (future): kept, flagged `needs_review` ("dated N days
  after upload"). Not corrected: the municipal bills are legitimately in the future.
- Nothing plausible: keep the model's date, flag it.
- **Every correction is flagged `needs_review`**, and the reason names all three facts:
  `Receipt date corrected from 2024-05-26 to 2026-05-26: printed "26-05-2026", uploaded
  2026-05-26.` A genuine backlog receipt (none exists today) is then one edit away,
  never silently rewritten.

Offline replay over the stored rows (no LLM, no as-printed field, which stored rows do
not have): 218/218 clean untouched, 3 bills flagged future, 19/19 of B corrected
exactly. A without the as-printed field is partly ambiguous (112's day/year swap gives
05-24, the year substitution 05-26), which is exactly why candidate 1 exists.

### 2. Schema: `receipt_date_as_printed`

New nullable string in `_FIELD_SCHEMAS`, **immediately before `receipt_date`**, with a
prompt line: "the document's date exactly as printed, same digits and separators, e.g.
`18/04/26`". Not stored in the database (no migration). It appears in the review
reason when it drove a correction. Field order is load-bearing (#67), so the gate below
decides.

### 3. Pipeline

`_run_pipeline` calls `resolve_receipt_date` right after extraction, before
`generate_stored_filename`, so the filed name carries the corrected date. The reason
joins `review_reason` the same way the totals check does.

### 4. Date edits rename the filed copy

`PATCH /documents/{id}` with `receipt_date` or `vendor_receipt_id` recomputes
`stored_filename`, writes the new filed copy, commits, then drops the old one:
the same `save → UPDATE → unlink` order and `STORAGE_MUTATION_LOCK` rule as
`_drop_superseded_filed_copy`. One shared helper, used by the endpoint and the repair.

### 5. One-off repair of the 31 (`scripts/repair_receipt_dates.py`)

Reads `{"id": "YYYY-MM-DD"}` (the verified file). A dry run (the default) prints
old → new date and filename. `--apply` goes through the same helper as the PATCH:
`edit_history` entry, `manually_edited = 1`, filed rename. **No LLM call, no reprocess.**
Run once in the container after deploy. The owner reviews the dry run first.

**Not done automatically:** re-sending the 25 already-exported documents. Clearing
their `last_exported_date` would put them in the next "since last export" batch as
corrected duplicates. The owner decides after seeing the list.

### 6. Gate

`scripts/compare_json_mode.py` (from #67) on the 31 misdated docs plus the stratified
clean sample, `--verified-dates .gstack/issue63-verified-dates.json`, master vs branch,
each with `--control`. The harness applies `resolve_receipt_date` in the branch arm (it
needs `submission_date`). Pass: year-correct on the misdated set rises from master's
~7/22 to near all; no regression on clean docs beyond the noise floor; the new field
does not move `tax_amount` nulls or other fields. About $4–5 of API calls, read-only.

## Not in scope

- The Hebrew calendar: no evidence it plays a part (every printed date is Gregorian).
- Changing the model or temperature.
- Bulk reprocessing (standing rule). The repair uses verified dates only.

## Tests

- `dates.py`: every mechanism row above as a table test, window edges (±180/+1),
  January-upload year rollback, Feb 29, garbage and null inputs, future bills kept.
- Pipeline: corrected date reaches `stored_filename` and `review_reason`; a clean
  document's status is unchanged.
- PATCH: a date edit renames the filed copy (old gone, new present, row updated first).
- Repair: dry run writes nothing; apply writes history, renames, is idempotent.
- Schema/prompt pin test updated for the new field.

## Eng review (2026-09-29): Step 0 and findings

Target: this plan (named explicitly). Report file: this plan.
Prior learnings applied: `confidence-blind-to-date-misreads` (10/10, 2026-09-23);
`stored-filename-is-llm-derived-not-deterministic` (10/10, 2026-09-13).

**Step 0: Scope Challenge**
1. Existing code reused: `totals_mismatch` / `format_totals_reason` (the review-reason
   pattern), `save_filed` + `_drop_superseded_filed_copy` / `_remove_superseded`
   (save → UPDATE → unlink under `STORAGE_MUTATION_LOCK`), `generate_stored_filename`,
   the PATCH `edit_history` loop, `compare_json_mode.py --verified-dates`.
2. Minimum: resolver + pipeline hook + as-printed field; PATCH rename; repair of the 31.
3. Complexity: ~12 files with tests, 2 new modules (`dates.py`, repair script). Gate
   tripped; no feature cuts proposed; structure question D1 asked.
4. Search: no new infrastructure or concurrency pattern. Date parsing uses stdlib
   `datetime.strptime` over an explicit format list. `python-dateutil` is present only
   transitively (not in `pyproject.toml`) and its fuzzy parsing is the opposite of what
   an audited date needs. [Layer 1]
5. TODOS: "Update extraction model to gemini-3.8-flash" (P2) may reduce the 2024 prior;
   the resolver is model-independent, so it does not block and is not bundled.
6. Completeness: full version planned (A, B, C mechanisms, repair, rename).
7. Distribution: no new artifact.

**Architecture findings**
- F1 [P1] (confidence: 9/10) `backend/config.py:52` `"gmail_unread_only": True,` and
  `backend/ingestion/gmail.py:594` `criteria = "UNSEEN" if unread_only else "ALL"`:
  backlog paths exist (Gmail "all messages", watched folder, web upload of old
  scans). The year substitution (candidate 3) would rewrite a genuinely old receipt
  to the upload year. No such document exists today (0 of 218 clean docs > 90 days),
  but the policy must be chosen. → D2.
- F2 [P2] (confidence: 9/10) Future-date flag: docs 65, 184, 266 (municipal bills,
  payment due date +37..+39 days) would be flagged; doc 26 (US `M/D/YY` print header,
  +29 days) is the one real future misread. → D3.
- F3 [P2] (confidence: 9/10) `backend/api/documents.py:235-248`: a PATCH carrying any
  totals key (the metadata form sends them on every save) recomputes `review_reason`
  from the totals check alone, so a date reason (like today's confidence reason) is
  erased by an unrelated save while status stays `needs_review`. → D4.
- F4 [P2] (confidence: 8/10) The PATCH rename needs a source file: the old filed copy
  can be missing (orphan cleanup, restore). Necessary implementation of the approved
  rename (D1): source from the old filed path, else `converted/<hash>.pdf`, else the
  original when it is a PDF; keep save → UPDATE → unlink. No question.
- F5 (confidence: 8/10) Month-first is deliberately never tried: for bill 184,
  `02/08/2026` month-first is Feb 8, inside the window, and would "correct" a right
  date. Recorded as a design constraint; doc 26 stays a flag, not a correction.

## Decision ledger

### R0: Scope and structure (complexity gate)
feature answers: none proposed; structure: A "Original arrangement" (D1, 2026-09-29);
accepted scope: dates.py resolver, schema field, pipeline hook, PATCH rename with a
shared helper, scripts/repair_receipt_dates.py (dry run default), harness applies the
resolver; pending remedies: R1-R3.

### R1: Correction policy for implausible dates (F1)
Finding: F1, P1, confidence 9/10, backend/config.py:52 + backend/ingestion/gmail.py:594, reviewer: Claude (eng review)
Plan baseline: original proposal, "Every correction is flagged needs_review" with the corrected date stored (§1)
Runtime evidence: offline replay over stored rows: year substitution right on 19/19 mechanism-B docs, 0 clean docs touched; 0 backlog docs exist
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 stored date when implausible | model's date, no flag | corrected date | model's date | corrected date |
| R1 status | processed | needs_review, reason names old/new/printed/upload | needs_review, reason suggests the date | processed, correction in edit_history only |
| R2 future flag | none | pending | pending | pending |
| R3 reason survives edits | n/a | pending | pending | pending |
Question D2:
D2 — When a receipt's date is implausible, should the app fix it or only flag it?
Project/branch/task: issue/63-receipt-date-year, the new date check at extraction time.
ELI10: 19 of the 31 bad receipts had the right day and month but the wrong year, and the model misreads even a printed "2026" as "2024", so only the upload date can fix them. If the app swaps in the upload year, those 19 come out right every time we have measured. The risk is a genuinely old receipt (say a 2023 one you upload today): it would be changed to 2026. Gmail's "all messages" mode and the watched folder can bring in old ones, though none exists today.
Stakes if we pick wrong: either a real old receipt gets a wrong year (visible, flagged), or every misread receipt waits for you to type the year by hand.
Recommendation: A because it was right on 19/19 real cases and the flag keeps a rare old receipt one edit away; B makes you fix every one by hand.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Correct and flag (recommended)
  ✅ The 19 misread years come out right with no typing; you just approve
  ✅ The reason shows the old date, the printed text and the upload date
  ❌ A genuinely old receipt is changed to this year until you undo it
B) Flag only, suggest the date
  ✅ Nothing is ever changed without you; old receipts keep their date
  ❌ Every misdated receipt (~12% so far) needs a manual date edit
C) Correct silently
  ✅ No review queue growth at all; edit_history keeps the old value
  ❌ An old receipt's year changes with nothing on screen to tell you
Net: auto-fix plus a visible flag, versus hands-off safety at the cost of manual edits.
Header: Date fixes
Options:
A) Correct and flag (recommended)
Store the corrected date and send the document to needs_review with a reason naming the old date, the date as printed and the upload date. Right on 19/19 measured cases; a rare genuinely old receipt is one edit to undo. CC ~no extra effort (plan as written).
B) Flag only, suggest the date
Keep the model's date, send to needs_review with the suggested date in the reason. Nothing changes without you; you type each correction (~12% of documents so far). CC same effort.
C) Correct silently
Store the corrected date, status stays processed, old value only in edit_history. No review load; an old receipt changed by mistake is invisible. CC same effort.

State: approved
Actual answer: A) Correct and flag (recommended), D2, 2026-09-29
Accepted scope: store the corrected date; status needs_review; reason names the old date, the date as printed and the upload date (plan §1 as written)
History: none

### R2: Future-dated receipts (F2)
Finding: F2, P2, confidence 9/10, docs 65/184/266 (bills, +37..+39 days) and 26 (US M/D/YY header, +29 days), reviewer: Claude (eng review)
Plan baseline: original proposal, a model date after upload + 1 day is kept and flagged needs_review (§1)
Runtime evidence: offline replay: 4 future-dated documents in 252; 3 are correct municipal bills, 1 (doc 26) is a real misread
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 correction policy | approved A (D2) | correct + flag | correct + flag | correct + flag |
| R2 future date handling | none | keep date, flag if > upload + 1 day | keep date, flag only if > upload + 62 days | no future check |
| R3 reason survives edits | n/a | pending | pending | pending |
Question D3:
D3 — Should a receipt dated after its upload be flagged for review?
Project/branch/task: issue/63-receipt-date-year, the date check at extraction time.
ELI10: A receipt can't be from the future, so a future date is usually a misread: doc 26 is a web page printed "4/5/26" (April 5, US style), and the model stored May 4. But your Mevaseret municipal bills print a payment due date 5-6 weeks ahead, and those are correct. In 252 documents there were 4 future dates: 1 wrong, 3 bills. The date is never changed either way, only flagged or not.
Stakes if we pick wrong: either about one correct bill every ~6 weeks lands in your review queue, or future-dated misreads like doc 26 slip through unflagged.
Recommendation: A because a bill costs one click to approve, and a silent wrong date costs a wrong tax month.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Flag any future date (recommended)
  ✅ Catches doc 26-style misreads the moment they arrive, nothing slips
  ✅ One rule, no magic number to tune, simplest to test and explain
  ❌ The bi-monthly municipal bills get flagged too (~3 in 5 months)
B) Flag only beyond 62 days
  ✅ The municipal bills (37-39 days ahead) stop showing up in review
  ❌ Doc 26 (29 days ahead) and any similar misread would pass silently
C) No future check
  ✅ Zero new review items from future dates, least code
  ❌ Future misreads are never caught; doc 26 stays wrong and unflagged
Net: a few one-click bill approvals versus missing the rare future misread.
Header: Future dates
Options:
A) Flag any future date (recommended)
Keep the model's date; send to needs_review when it is more than 1 day after upload, reason "dated N days after upload". Catches doc 26; flags the 3 municipal bills seen so far (~one per 6 weeks). CC same effort.
B) Flag only beyond 62 days
Keep the date; flag only when more than 62 days after upload. Bills stop being flagged; doc 26 (+29 days) and similar misreads pass unflagged. CC same effort.
C) No future check
Keep the date and never flag future dates. No extra review items; future misreads are not caught. CC slightly less.

State: approved
Actual answer: A) Flag any future date (recommended), D3, 2026-09-29
Accepted scope: keep the model's date; needs_review when it is more than 1 day after upload, reason "dated N days after upload"; no month-first re-reading (F5)
History: none

### R3: The date reason must survive unrelated edits (F3)
Finding: F3, P2, confidence 9/10, backend/api/documents.py:235-248 (`elif {"total_amount", "subtotal", "tax_amount"} & changes.keys():` then `set_clauses.append("review_reason = NULL")` when totals agree), reviewer: Claude (eng review)
Plan baseline: original proposal is silent; the date reason "joins review_reason the same way the totals check does" (§3)
Runtime evidence: the metadata form sends the three totals keys on every save (comment at documents.py:236-239), so any save of a document whose totals agree sets review_reason to NULL, erasing a date reason (and, today already, a confidence reason) while status stays needs_review
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 correction policy | approved A (D2) | same | same | same |
| R2 future flag | approved A (D3) | same | same | same |
| R3 reason on a PATCH with totals keys | totals-only recompute, other reasons erased | recompute the totals part, keep the date and confidence parts; drop the date part only when receipt_date itself is edited, then re-check the new date | unchanged (date reason erased by any form save) | reasons stored as a JSON list in a new column (migration), each recomputed or kept by kind |
Question D4:
D4 — Should an unrelated edit keep the "check the date" warning?
Project/branch/task: issue/63-receipt-date-year, the review reason shown on a flagged document.
ELI10: When the app corrects or flags a date, it writes a sentence explaining why (for example "corrected from 2024-05-26 to 2026-05-26"). Today, saving the edit form recalculates that sentence from the totals check alone, so fixing a vendor typo wipes the date warning while the document stays in the review queue with no explanation. The same thing already happens to the low-confidence warning.
Stakes if we pick wrong: you open a flagged receipt, fix something else, and the reason it was flagged disappears before you checked the date.
Recommendation: A because it keeps each warning until the thing it's about is edited, with no database change.
Completeness: A=9/10, B=4/10, C=10/10
Pros / cons:
A) Keep other warnings (recommended)
  ✅ The date warning stays until you edit the date itself, then re-checks it
  ✅ Fixes the same bug for the existing low-confidence warning at no extra cost
  ❌ Warnings are still one text column, split by a known prefix per kind
B) Leave as today
  ✅ No change to the edit endpoint; smallest diff in this branch
  ❌ Any form save erases the date warning while the document stays flagged
C) Structured reasons column
  ✅ Each warning is its own record, no text splitting anywhere
  ❌ A migration plus API, pipeline and UI changes for a one-sentence field
Net: a small, tested prefix convention now versus a migration for cleaner storage.
Header: Edit warnings
Options:
A) Keep other warnings (recommended)
On a PATCH with totals keys, recompute only the totals sentence and keep the date and confidence sentences; drop the date sentence only when receipt_date is edited, then re-run the plausibility check on the new date. Sentences are identified by fixed prefixes owned by dates.py and extract.py. Also fixes the confidence warning. CC ~20 min + tests.
B) Leave as today
No change to documents.py; a form save recomputes the reason from totals alone and erases the date warning while status stays needs_review. CC none.
C) Structured reasons column
New migration: review_reasons JSON list; pipeline, PATCH, API model and DocumentDetailPage read and write it by kind. Cleanest storage; widest diff. CC ~1-1.5h + tests.

State: approved
Actual answer: A) Keep other warnings (recommended), D4, 2026-09-29
Accepted scope: a PATCH carrying totals keys recomputes only the totals sentence and keeps the date and confidence sentences; the date sentence is dropped only when receipt_date is edited, and the new date is re-checked; sentences identified by fixed prefixes owned by dates.py and extract.py; tests for each
History: none

### R4: Regression contract for the date check (IRON RULE)
Finding: T1, P1, confidence 9/10, tests/test_pipeline.py:26 (`INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel) VALUES (?, ?, ?, 'pending', 'web_upload')`, so submission_date defaults to now) with MOCK receipt_date "2026-01-15": 257 days before today, so every pipeline fixture would be flagged, and the gap grows with the calendar. Reviewer: Claude (eng review)
Plan baseline: original proposal, "a clean document's status is unchanged" (Tests)
Runtime evidence: the repo is PUBLIC (gh repo view), so real purchase dates must not be committed as fixtures
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1-R3 | approved (D2-D4) | same | same | same |
| R4 contract items 1-4 (below) | none | adopted | adopted | adopted |
| R4 corpus-shaped table test | none | synthetic table from the measured gap shapes (0-7, 8-90 days, +37..+39 bills, 1-8 years) | real (model date, upload date) pairs from the 252 docs, dates only | none |
Contract items: (1) a date inside [upload − 180 d, upload + 1 d] keeps its value and the document keeps today's status; (2) the resolver never reads the wall clock, only submission_date, and pipeline fixtures pin submission_date; (3) a PATCH that changes neither receipt_date nor vendor_receipt_id leaves stored_filename and filed/ untouched; (4) the existing review-reason tests (test_documents_api.py:168-235) pass unchanged, except the intended D4 change (other sentences kept).
Question D5:
D5 — What must the date check provably leave alone?
Project/branch/task: issue/63-receipt-date-year, regression tests before implementation.
ELI10: The new check touches every document, so we pin what must NOT change: normal receipts keep their date and status, the check never looks at today's clock (only the upload date, so tests don't rot as months pass), editing a vendor name never renames files, and the existing warning tests keep passing. The open part is how to prove "normal receipts are untouched" at scale: with a made-up table shaped like your real data, or with your real dates. The repo is public on GitHub.
Stakes if we pick wrong: a clean receipt silently lands in review or gets a new date, or your purchase dates end up in a public repo.
Recommendation: A because it proves the same shapes the real data showed without publishing your purchase dates.
Completeness: A=9/10, B=10/10, C=7/10
Pros / cons:
A) Contract + synthetic table (recommended)
  ✅ Covers every gap shape measured (same-day, 8-90 days, bills, 1-8 years off)
  ✅ No personal data in a public repo; runs in milliseconds
  ❌ Proves the shapes, not your exact 252 rows (the offline replay did that once)
B) Contract + real-date replay
  ✅ Replays all 252 real rows forever, strongest regression guard
  ❌ Commits your purchase dates to a PUBLIC GitHub repository
C) Contract only
  ✅ Least test code; the four contract items are still enforced
  ❌ No table: a window-edge bug could flag clean receipts unnoticed
Net: near-full proof without publishing data, versus full proof that leaks purchase dates.
Header: Regressions
Options:
A) Contract + synthetic table (recommended)
Adopt contract items 1-4, fix the pipeline fixtures to pin submission_date, and add a table test in test_dates.py generated from the measured gap shapes (0-7 d, 8-90 d, future bills +37..+39 d, 1-8 years off, YY/MM/DD swaps). No real data committed. CC ~20 min.
B) Contract + real-date replay
Adopt items 1-4 and commit a fixture of the 252 real (model date, upload date) pairs, dates only, asserting 218 unchanged / 3 flagged / 19 corrected. Strongest guard; publishes purchase dates in a public repo. CC ~15 min.
C) Contract only
Adopt items 1-4 with targeted unit tests only (window edges, each mechanism once); no corpus-shaped table. CC ~10 min.

State: approved
Actual answer: A) Contract + synthetic table (recommended), D5, 2026-10-01 (first asked 2026-09-29; that call was interrupted by a session end and re-asked)
Accepted scope: contract items 1-4 above; pipeline fixtures pin submission_date; a synthetic corpus-shaped table test in tests/test_dates.py (0-7 d, 8-90 d, +37..+39 d bills, 1-8 years off, YY/MM/DD swaps); no real purchase dates committed
History: D5 asked 2026-09-29, tool call interrupted (session ended), no answer recorded

### R5: Eval gate for the new schema field (→EVAL)
Finding: T2, P1, confidence 8/10, plan §2 (`receipt_date_as_printed` immediately before `receipt_date`) and the #67 record: a field reorder measured "2026" read as "2020" and more null tax_amount (CLAUDE.md, LLM JSON handling). Reviewer: Claude (eng review)
Plan baseline: original proposal §6: compare_json_mode.py master vs branch, each with --control, 31 misdated + stratified clean sample, --verified-dates, ~$4-5
Runtime evidence: the harness and --verified-dates exist (a5442d6); it scores whatever receipt_date the arm returns, so it cannot tell the field's effect from the resolver's unless it reports both
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1-R4 | approved (D2-D5) | same | same | same |
| R5 paid A/B gate | none | master vs branch, --control both, 31 misdated + stratified clean, scores raw model date AND resolved date separately, blocks on field drift (tax_amount nulls etc.) | none: offline replay + unit tests only | gate on the 31 misdated only, no clean sample |
Question D6:
D6 — Run the paid A/B gate before merging?
Project/branch/task: issue/63-receipt-date-year, measuring the new "date as printed" field on real receipts.
ELI10: Most of the fix is plain code, proven by tests. But adding a field to what the model fills in can change how it reads everything else: in #67 moving one field made it read "2026" as "2020" and miss more VAT amounts. The gate re-reads ~50 of your receipts with the old and new code (read-only, nothing stored), twice each to measure noise, and scores dates against the 31 I checked by eye. It reports the model's own date and the corrected date separately, so we see what the field does on its own.
Stakes if we pick wrong: skip it and a subtle model regression (VAT, totals, dates on good receipts) ships unseen; run it and it costs ~$4-5 and ~1 hour.
Recommendation: A because #67 proved field changes move other fields, and this is the only check that sees it.
Completeness: A=10/10, B=5/10, C=7/10
Pros / cons:
A) Full gate (recommended)
  ✅ Catches a field side-effect on VAT, totals or good dates before you see it
  ✅ Separates the model's own date from the code's correction in one report
  ❌ About $4-5 of API calls and an hour of wall time
B) No paid gate
  ✅ Free and fast; the resolver itself is fully unit tested
  ❌ Blind to how the new field changes the model's other answers
C) Misdated set only
  ✅ Cheaper (~$2-3), still shows whether the field helps the bad dates
  ❌ Misses regressions on the clean receipts, which are 88% of your data
Net: ~$5 and an hour to see the model side-effects, versus trusting the field blind.
Header: Eval gate
Options:
A) Full gate (recommended)
Run compare_json_mode.py master vs branch, --control on both, on the 31 misdated plus the stratified clean sample, --verified-dates; report raw model receipt_date and resolved date separately; block on field drift beyond noise (tax_amount nulls, totals). Read-only. ~$4-5, ~1h. CC ~20 min to add the dual date score.
B) No paid gate
Rely on unit tests and the offline replay; no LLM calls. Free; the new field's effect on other fields is unmeasured until production.
C) Misdated set only
Run the gate on the 31 misdated only, both arms with --control. ~$2-3; shows the field's effect on bad dates, not on clean receipts.

State: approved
Actual answer: A) Full gate (recommended), D6, 2026-10-01
Accepted scope: compare_json_mode.py master vs branch, --control on both, 31 misdated + stratified clean sample, --verified-dates; the branch arm reports the raw model receipt_date and the resolved date as separate scores; blocks on field drift beyond the noise floor; read-only, ~$4-5
History: none

## Eng review: sections 2-4

**Code quality findings** (no new choices; each is necessary implementation of an approved row)
- Q1 (confidence: 9/10) `backend/processing/pipeline.py:106` `review_reason = f"{review_reason}. {detail}" if review_reason else detail`:
  the join is about to gain a third caller (date) and a fourth (PATCH, R3). One helper
  owns the sentence list (join, split by the fixed prefixes R3 approved).
- Q2 (confidence: 9/10) Filename + filed copy: the pipeline's `save_filed` →
  UPDATE → `_drop_superseded_filed_copy` sequence becomes one helper shared by the
  pipeline, the PATCH and the repair script (R0). No second copy of the ordering.
- Q3 (confidence: 8/10) The pipeline reads `doc["submission_date"]`; `_run_pipeline` gets
  `doc` from the queue row. Confirm the SELECT carries it; tests pin it (R4 item 2).
- Q4 (confidence: 8/10) CLAUDE.md "Gotchas: documents.stored_filename ... (measured: the
  *year* moves ... issue #63)" and "LLM JSON handling" must be updated in this branch.

**Test review** (pytest; CLAUDE.md `## Testing`)

```
CODE PATHS (planned)                                       USER FLOWS
[+] processing/dates.py                                     [+] New receipt arrives
  ├── parse_printed_date()                                    ├── [GAP] clean date → processed, unchanged (R4.1)
  │   ├── [GAP] D/M/YY, D.M.YYYY, D-M-YY, YYYY-MM-DD          ├── [GAP] YY/MM/DD misread → corrected + flagged
  │   ├── [GAP] 2-digit year → 20YY                           ├── [GAP] wrong year → upload year + flagged
  │   ├── [GAP] invalid day (31/02), Feb 29, garbage, None     └── [GAP] future bill → kept + flagged (D3)
  │   └── [GAP] never month-first (F5: "02/08/2026")         [+] Owner edits a flagged receipt
  └── resolve_receipt_date()                                  ├── [GAP] fix the date → filed/ renamed, sentence dropped,
      ├── [GAP] in window → unchanged, no reason                │        new date re-checked (D4)
      ├── [GAP] window edges: −180 d, −181 d, +1 d, +2 d        ├── [GAP] fix vendor only → filed/ untouched (R4.3),
      ├── [GAP] candidate 1 (printed) wins over model           │        date sentence kept (D4)
      ├── [GAP] candidate 3 upload year / year before (Jan)     └── [GAP] approve → reason cleared (existing ★★★ :210)
      ├── [GAP] future → kept + flag (D3)                     [+] One-off repair
      ├── [GAP] nothing plausible → kept + flag                 ├── [GAP] dry run writes nothing
      ├── [GAP] model date None / not ISO                        ├── [GAP] --apply: history, manually_edited, rename
      └── [GAP] synthetic corpus table (R4/D5)                  └── [GAP] second --apply is a no-op
[+] processing/extract.py
  ├── [★★★ TESTED → update] prompt lists schema fields in order — test_extract.py:340
  └── [GAP] parse keeps receipt_date_as_printed; null tolerated
[+] processing/pipeline.py
  ├── [GAP] corrected date reaches stored_filename + review_reason
  ├── [★★★ TESTED → keep] joins both review reasons — test_pipeline.py:111
  └── [GAP] fixtures pin submission_date (R4.2)
[+] api/documents.py PATCH
  ├── [GAP] receipt_date / vendor_receipt_id edit → rename, save→UPDATE→unlink order
  ├── [GAP] old filed copy missing → source from converted/original (F4)
  ├── [★★★ TESTED → keep] totals reason recompute — test_documents_api.py:168-235
  └── [GAP] totals recompute keeps date + confidence sentences (D4)
[+] scripts/compare_json_mode.py
  └── [GAP] [→EVAL] branch arm scores raw and resolved date separately (D6)

COVERAGE: 4/30 paths have tests today (all pre-existing)  |  GAPS: 26 (1 eval)
```

Regression rule: R4 (D5) is the approved contract; items 1-4 are CRITICAL tests.

**Performance review:** No issues found. The resolver is O(1) per document; the PATCH
rename is one file copy and one unlink, the same cost as a reprocess.

**Failure modes**
| New path | Realistic failure | Test | Handling | User sees |
|---|---|---|---|---|
| resolver | model returns `"2026/05/26"` or a number | planned | treated as unparseable → flag | reason in review |
| resolver | backlog receipt year rewritten | planned | flagged (D2) | reason names old date |
| PATCH rename | old filed copy missing | planned (F4) | source fallback | nothing (works) |
| PATCH rename | crash between UPDATE and unlink | existing pattern | spare file, `filed_orphans` | nothing; verify reports it |
| repair | run twice | planned | idempotent | "0 changed" |
| gate | field drift on clean docs | eval (D6) | blocks merge | n/a |
No critical gaps (every failure has handling and is visible).

**NOT in scope**
- Re-sending the 25 already-exported receipts: the owner decides after the repair's list.
- Structured `review_reasons` column (D4 option C): prefix convention chosen instead.
- Using the email `Date:` header as the anchor for email documents: submission_date is
  within days for every email document today.
- Model upgrade (TODOS "gemini-3.8-flash"): independent; the resolver does not depend on it.

**What already exists**: listed in Step 0 item 1; the plan reuses all of it and rebuilds none.

**Worktree parallelization:** Sequential implementation, no parallelization
opportunity (dates.py feeds the pipeline, PATCH and repair; the gate needs the branch).

### R6: TODO candidate, backlog import mode
Finding: D2 con (approved A): a genuinely old receipt is corrected to the upload year and flagged. Reviewer: Claude (eng review)
Plan baseline: none (new proposal)
Runtime evidence: 0 backlog documents in 252; Gmail "ALL" mode (`gmail.py:594`) and the watched folder can import old receipts in bulk
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R6 backlog import mode | none | TODOS.md entry (P3) | not recorded | built in this PR |
Question D7:
D7 — Record a TODO for a "backlog import" mode?
Project/branch/task: issue/63-receipt-date-year, follow-up work from the D2 decision.
ELI10: You chose "correct and flag", which is right for everyday uploads. If you ever bulk-import a stack of old receipts (Gmail "all messages", or a folder of last year's scans), every one would be changed to this year and land in review, and you'd undo each by hand. A backlog mode would switch the year correction off for one import. Nothing like that has happened yet, so the question is only whether to write it down.
Stakes if we pick wrong: a future bulk import produces dozens of wrong-year receipts to undo, with no note that this was foreseen.
Recommendation: A because it costs one TODO entry now and saves rediscovering the problem during an import.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Add to TODOS.md (recommended)
  ✅ The known limit of D2 is written down with where to start
  ✅ Zero code now; picked up only if a backlog import is ever planned
  ❌ One more open item in TODOS.md that may never be needed
B) Skip
  ✅ Keeps TODOS.md to things likely to happen
  ❌ A future bulk import rediscovers the problem the hard way
C) Build it now
  ✅ Ready before any import; one setting checked by the resolver
  ❌ Widens this PR with a setting, UI toggle and tests nobody needs today
Net: a one-paragraph note now versus building or forgetting an unneeded mode.
Header: Backlog TODO
Options:
A) Add to TODOS.md (recommended)
Add a P3 TODO "Backlog import mode: skip the upload-year correction" with what/why/context (D2 con, gmail.py:594, watched folder) and where to start (a setting read by resolve_receipt_date). No code now.
B) Skip
Record nothing; the limitation stays only in this plan's D2 record.
C) Build it now
Add a setting (e.g. date_year_correction on/off) read by resolve_receipt_date, a Settings toggle and tests, in this PR. CC ~30-40 min.

State: pending
Actual answer: unanswered
Accepted scope: none
History: none
