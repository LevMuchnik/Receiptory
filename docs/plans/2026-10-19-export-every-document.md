# Export every document in the period

**Status:** draft for /plan-eng-review
**Origin:** TODOS.md "Export every document in the period, whatever its status"; owner rule 2026-10-19 (documents are the key): "everything submitting during the export period must be exported... Accountants will process exported receipts themselves."

## Problem

A period export (`POST /api/export` without `document_ids`) loses documents in three ways, all silently:

1. **Status filter.** `conditions.append("d.status = 'processed'")` keeps out `pending`, `processing`, `needs_review` and `failed`. Live DB 2026-10-19: 1 live non-processed document (id 274, a failed `.mht` invoice, no filed copy).
2. **No issue date.** With "Issue Date" selected (`date_basis = "receipt"`), `receipt_date IS NULL` never matches a range. Live: 9 live documents have no `receipt_date` (8 processed, filed as `0000-00-00-...pdf`, plus id 274). They reach the accountant only through an "Upload Date" or "since last export" run. `test_export_null_receipt_date_ingestion` pins the exclusion as intended behaviour today.
3. **No filed copy.** A row with no `stored_filename`, or whose filed PDF is missing, is left out of the zip (its CSV row stays, unstamped). The original is still in `storage/originals/` and is the actual document.

## Changes

### C1. No status filter on period exports
Drop the `status = 'processed'` condition. Only `is_deleted = 1` keeps a document out. The explicit `status` request field stays (API callers; the UI sends none).

### C2. A document with no valid issue date falls under its upload date
On the receipt basis the period column becomes `CASE WHEN d.receipt_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' THEN substr(d.receipt_date, 1, 10) ELSE substr(d.submission_date, 1, 10) END`, in the WHERE and the ORDER BY. `_export_basename`'s row-derived range uses the same rule (one Python helper mirroring the SQL). `receipt_date` is model output stored verbatim, so an empty or malformed value is treated like NULL. `submission_date` is NOT NULL (migrations/001_initial_schema.sql:71).

Limit (stated, not fixed): on the Issue Date basis a document can still move between months after export (a pending document gets its issue date once processed, or a late upload carries an old date). Only the Upload Date basis, the owner's normal one, guarantees one fixed period per document.

### C3. File fallback: filed copy, else the original
For each row, an ordered list of candidates `[(path, arcname), ...]`, tried in turn by the loop: the filed copy (`get_filed_path`, unchanged checks), then the original (`atomic.find_original(storage, file_hash)`). A candidate that vanishes during `zf.write` (`FileNotFoundError`, concurrent reprocess) moves on to the next one. The first one written is recorded as `exported_file[id]`.

The original's zip name reuses `filing.download_name(stored_filename, original_filename, id, ext)` (the hardened namer behind `GET /documents/{id}/file/original`: Content-Disposition values, control characters, double extensions, `.bin`/dotless), with `ext` from the path found. With a valid filed name it already carries date and hash; without one it is prefixed `<YYYY-MM-DD upload date>-<hash[:8]>-` so two "receipt.pdf" uploads cannot collide. It goes in the same category folder as a filed copy would (system categories have no section, so `other/`).

Not used: `storage/converted/<hash>.pdf`. It is a derived rendering; the original is the document. A `.mht` opens in Edge and Word. Non-PDF originals (`.html`, `.bin`) go to the accountant as they are; 0 such live today besides one `.mht`.

`omitted` keeps its meaning: no candidate could be added. Its row is still in the CSV and still unstamped.

### C4. Stamping
A document is stamped `last_exported_date` when its file (either kind) is in the zip, whatever its status. A `pending` or `failed` document delivered as its original is therefore not offered again by "since last export" after it is processed. Consistent with the owner's 2026-10-19 decision on unconverted rows: delivered once, never held back, never sent twice.

### C5. CSV/XLSX: where the file is
Append one column, `exported_file`: the path inside the zip, empty when omitted. Appended after the #72 columns so positional readers keep working. Needed because an original has no `stored_filename` to match on.

### C6. Tell the owner when a document had no file (D6)
The response carries `X-Export-Omitted: <n>` (exposed to the SPA); after the download the Export page and the Documents page export show a warning: "<n> document(s) had no file on disk and are only in the spreadsheet". They stay unstamped (unchanged).

### C7. Keep the first export date (D7)
`SET last_exported_date = COALESCE(last_exported_date, ?)`: a re-export no longer moves it. `since_last_export` only tests NULL, so it behaves the same.

## Not changing
- `document_ids` exports (already ignore status; they gain C3, C5, C6 and C7).
- Export page controls. It sends no status; nothing to toggle. Only the C6 warning and the "Generates PDF + CSV package" wording change.
- Folder layout for filed copies.

## Tests (tests/test_export.py)
- A `failed` document with no `stored_filename` and an original is in the zip under the original name, and is stamped.
- A `needs_review` document with a filed copy is exported.
- A `processed` document whose filed copy is missing falls back to the original and is stamped (replaces the unstamped expectation in `test_a_document_whose_filed_copy_is_missing_is_not_marked_exported` for the case where an original exists; the case with neither file keeps it).
- A document with neither file: CSV row present, `exported_file` empty, unstamped.
- A deleted document is never exported.
- Receipt basis: NULL `receipt_date` lands in the month of its upload date and not in another month (rewrites the second half of `test_export_null_receipt_date_ingestion`).
- An invalid `file_hash` (not 64 hex) never reaches the zip (`find_original` returns None → omitted).
- `exported_file` column is last, in CSV and XLSX.

## Docs
ExportPage.tsx description text; CLAUDE.md "Documents are the key" sentence gains the export rule; v1 spec export section (status filter, fallback, column).

---

# Eng review (2026-10-19, /plan-eng-review)

**Target:** this plan, `docs/plans/2026-10-19-export-every-document.md` (branch `issue/export-all-documents`, base `8142735`).

## Scope Challenge

- Complexity: 5 files touched (`backend/api/export.py`, `tests/test_export.py`, `CLAUDE.md`, `docs/specs/2026-03-28-receiptory-v1-design.md`, `TODOS.md`), 0 new classes/services. Complexity gate not tripped.
- Reuse: `atomic.find_original` (backend/atomic.py:174) already locates originals under any extension and refuses a non-hex hash; `storage.get_filed_path` keeps the filed-copy checks; `_slugify` (export.py:23) for the arcname. Nothing new to build.
- Prior approval: C1 and C3 come from the TODO the owner approved during /ship #72 (D9: "Separate PR after #72"), whose text says "Include every non-deleted document in the period, using its original file (`atomic.find_original`) when it has no filed copy".

Findings:
1. [P1] (confidence: 9/10) backend/ingestion/gmail.py:351-360 (`status = "pending" if authorized else "needs_review"` ... `category_id = cat["id"]` for `unauthorized_sender`). Dropping the status filter (C1) exports mail from strangers to the accountant. Live: 0 such documents today.
2. [P2] (confidence: 9/10) tests/test_export.py:164-168 pins "Silently excluded with receipt basis (receipt_date IS NULL)". C2 reverses an existing tested behaviour; needs the owner's call.
3. [P2] (confidence: 8/10) export.py:300-307 stamps every delivered row. With C1, a `pending`/`processing` document is delivered as its original and stamped, so it never comes back with its extracted fields once processed. C4 is a new policy, not covered by the 2026-10-19 ruling on unconverted rows.
4. [P2] (confidence: 8/10) TODOS.md:84 says bundle "#72 review follow-ups" into this PR (owner D10/D11 during /ship #72); today's request names only the export.

## Decision ledger

### R1: bundle the #72 follow-ups
Finding: 4. Plan baseline: TODOS.md:84, bundle (owner D10/D11, /ship #72). Runtime evidence: n/a.
Comparison grid: | R1 | current: bundle | A: export only, follow-ups next PR before #73 | B: bundle |
Question D2: Bundle the #72 follow-ups into this PR? Recommendation: A.
Header: Follow-ups
Options:
A) Export only (recommended)
B) Bundle, as decided in #72
State: approved
Actual answer: owner 2026-10-22: "1. ok." to the recommendation (A)
Accepted scope: A: this PR is the export change only; "#72 review follow-ups" stays in TODOS.md as the next PR, before #73

### R2: receipt basis, no issue date
Finding: 2. Plan baseline: C2 proposed (fall back to upload date). Runtime evidence: excluded today (export.py:96, test_export.py:164); 9 live documents affected [measured: read-only DB 2026-10-19].
Comparison grid: | R2 | current: excluded | A: upload date | B: excluded | C: every Issue Date export |
Question D3: Where does a document with no issue date go in an Issue Date export? Recommendation: A.
Header: No date
Options:
A) Its upload date (recommended)
B) Left out, as today
C) Every Issue Date export
State: defaulted (owner did not object after being told it would apply; not an explicit approval)
Actual answer: owner 2026-10-22: "we typically export by submission date, not issue date" (no objection to the fallback; reviewer applies A as the default, owner may still overrule)
Accepted scope: A: on the Issue Date basis an undated document falls under its upload date (WHERE, ORDER BY, zip name); rewrite the second half of test_export_null_receipt_date_ingestion

### R3: stamping a document delivered before it was processed
Finding: 3. Plan baseline: C4 proposed (stamp all delivered). Runtime evidence: export.py:300-307 stamps every delivered row.
Comparison grid: | R3 | current: n/a (not exported) | A: stamp all | B: hold back pending/processing | C: hold back every non-processed |
Question D4: Stamp a document delivered before it finished processing? Recommendation: A.
Header: Stamping
Options:
A) Stamp it (recommended)
B) Hold back pending only
C) Hold back all unprocessed
State: approved
Actual answer: owner 2026-10-22: "yes. agree." (A)
Accepted scope: A: stamp every document whose file is in the zip, whatever its status

### R4: unauthorized email senders
Finding: 1. Plan baseline: C1 (no status filter) drops their only barrier. Runtime evidence: gmail.py:351-360; 0 live.
Comparison grid: | R4 | current: excluded (needs_review) | A: exclude while in unauthorized_sender | B: export too |
Question D5: Export documents from unauthorized email senders? Recommendation: A.
Header: Strangers
Options:
A) Keep them out (recommended)
B) Export them too
State: approved
Actual answer: owner 2026-10-22: "status filter (google label) should remain the determinate". Live gmail_authorized_senders is [] (every sender authorized, gmail.py:87-89), so the Gmail labels (Receipt, Invoice) decide what is ingested; anything ingested is submitted. (B)
Accepted scope: B: no category exclusion; only is_deleted keeps a document out of a period export

Scope Challenge result: scope accepted as-is (R1 keeps the follow-ups out, which was always a separate TODO entry; no part of the export plan was cut).

Reality rows:
- REALITY: premise-table pass 6 premises, unverified=0: status filter export.py:83; null receipt_date excluded test_export.py:164; 9 undated live docs and 1 failed live doc [measured: read-only DB 2026-10-19]; find_original refuses non-hex hashes atomic.py:200; submission_date NOT NULL migrations/001_initial_schema.sql:71; gmail_authorized_senders = [] [measured: settings table] so every sender is authorized gmail.py:87-89
- REALITY: already-done pass no commit on origin/master since 2026-10-19 touches backend/api/export.py (git log origin/master --since=2026-10-19)
- REALITY: binding-decisions pass owner rule "documents are the key" (memory + learning receiptory-documents-are-the-key, 2026-10-19) and the stamp-every-delivered-row ruling (export.py:300-302) are kept; R1-R4 answers recorded above
- REALITY: surface-check pass POST /api/export route export.py:79, ExportRequest fields models.py:169-180, atomic.find_original(storage, file_hash) atomic.py:174, storage.get_filed_path(stored_filename, data_dir) storage.py:54
- REALITY: numbers pass 312 live docs, 311 processed + 1 failed; 9 without receipt_date; originals/ 100M, filed/ 86M [measured: du 2026-10-22]; 314 pdf / 56 jpg / 17 png / 1 mht originals [measured: ls]
- REALITY: deferred-asks pass TODO entry asks: no status filter (C1), original fallback (C3), keep status column (unchanged EXPORT_CSV_FIELDS), omitted/since_last_export rules (C3/C4); the "#72 review follow-ups" bundle deferred by R1
- REALITY: pr-count pass one PR (no .gstack/ship-policy.json; default one PR per wave)

## Section 1: Architecture

- A1 [P2] (confidence: 8/10) backend/api/export.py:223-235 (`except FileNotFoundError: omitted.add(row["id"])`). A filed copy that vanishes mid-export (concurrent reprocess renaming it) is still omitted; under C3 it should fall back to the original like a missing filed copy. Part of C3's approved contract (TODO D9: "using its original file when it has no filed copy"); no new choice.
- A2 [P3] (confidence: 7/10) backend/api/export.py:155 (`buf = io.BytesIO()`). The zip is built in memory. C3 adds originals only for rows without a usable filed copy (1 live today), so the in-memory size barely moves (originals/ is 100M against filed/ 86M in total). Pre-existing; no change.

Data flow after the change:

```
POST /api/export
  -> WHERE is_deleted = 0 [+ period on submission_date | COALESCE(receipt_date, date(submission_date))] [+ optional filters]
  -> for each row:
       stored_filename ok and filed file present  -> zip <section>/<cat>/<stored>          (stamp)
       else, or filed file vanished mid-write      -> find_original(storage, file_hash)
            found                                  -> zip <section>/<cat>/<date>-<slug>-<hash8><ext> (stamp)
            not found / bad hash                   -> omitted (CSV row kept, exported_file empty, NOT stamped)
  -> metadata.csv / metadata.xlsx (+ exported_file, last column)
  -> UPDATE last_exported_date for delivered rows
```

Dispositions: A1 accepted (covered by C3 approval D9). A2 no change.

## Section 2: Code quality

- Q1 [P2] (confidence: 8/10) backend/api/export.py:158-245. The per-row loop already has three nested exits; a third file source makes it unreadable. Move the choice into one local function `_file_for_row(row, data_dir) -> (path, arcname) | None` in export.py, with the loop doing only `zf.write` + `omitted`. Single caller, so this is structure, not shared code.
- Q2 [P2] (confidence: 8/10) proposed original arcname. `original_filename` can be NULL, empty, all punctuation or long (Hebrew `.mht` names are 25+ chars); slug falls back to `document` and is capped at 80 characters; the extension comes from the path `find_original` returned (lowercased), never from `original_filename`.
- Q3 resolved (confidence: 9/10) migrations/001_initial_schema.sql:71 `submission_date TEXT NOT NULL DEFAULT (...)`, so the C2 COALESCE always yields a date. No null branch needed.

Dispositions: Q1, Q2 accepted as implementation of C3 (D9); Q3 no change.

## Section 3: Tests

Framework: pytest (CLAUDE.md Testing), `tests/test_export.py`, 21 tests today.

```
CODE PATHS (backend/api/export.py)                       STATUS
[+] WHERE
  ├── no status filter on period exports (C1)            [GAP] needs_review/failed rows exported
  ├── explicit body.status still filters                 [GAP] keep, one assertion
  ├── is_deleted = 1 never exported                      [GAP]
  └── receipt basis COALESCE (C2)                        [★★ test_export_null_receipt_date_ingestion, second half flips]
[+] _file_for_row
  ├── filed copy present                                 [★★★ test_export_zip_structure + escape tests]
  ├── stored_filename escapes filed/ -> original?        [★★★ :249 keeps omission when no original; GAP with original]
  ├── no stored_filename -> original                     [GAP]
  ├── filed copy missing -> original                     [GAP] (:344 keeps "neither file" case)
  ├── filed copy vanished mid-write -> original          [GAP] monkeypatch zf.write to raise FileNotFoundError once
  ├── bad file_hash -> omitted, unstamped                [GAP] (fixtures already use 'h1')
  └── arcname slug: None / Hebrew / long name            [GAP]
[+] CSV/XLSX exported_file last column                   [GAP] extend :416
[+] stamping: delivered original stamped (C4)            [GAP]
USER FLOW
  └── Export page "Upload Date" month incl. failed doc   [GAP] same test as "no stored_filename"
COVERAGE: 3/14 paths today
```

Value cards for the new tests are in the Test Plan artifact. Regression contract: keep every current assertion except the two intentional flips (second half of :144 for C2; :344 keeps "neither file -> unstamped" and gains "original -> stamped").

Dispositions: all GAPs accepted as required proof of C1-C5 (D9, R2-R4).

## Section 4: Performance

- P1 [P3] (confidence: 8/10) backend/atomic.py:203-210. `find_original` globs `originals/` for a non-`.pdf` original; called only for rows without a usable filed copy (1 live). No change.

No other issues: one SELECT, no per-row queries; UPDATE is one statement.

## Outside Voice

Codex CLI not installed (CODEX_MODE: not_installed). Native fallback: Claude Plan subagent, completed inside the five-minute timer. Source in-host; not outside coverage.

Findings and dispositions:
1. High, R4 ambiguous ("status filter (google label) should remain the determinate"). The owner's reading was put back to them in plain words on 2026-10-22 ("the Gmail label decides ... only deleting a document keeps it out") and they answered the next item without correcting it. Kept B. Recorded consequence: if `gmail_authorized_senders` is ever set, mail from unlisted senders (`unauthorized_sender`, `needs_review`) is exported too.
2. Medium-High, helper shape cannot do the mid-write fallback. Accepted: ordered candidate list (C3), `exported_file` map.
3. Medium, malformed non-NULL receipt_date escapes COALESCE; "exactly one month" overstated. Accepted: GLOB rule and stated limit (C2).
4. Medium, rebuilds `filing.download_name`. Accepted (C3); verified backend/processing/filing.py:112.
5. Medium, auth-wall / page captures (`needs_review`, never queued) exported as documents. Kept per the owner rule (the owner submitted that link; the CSV `status` column says needs_review). Recorded, 0 live.
6. Medium, the last omission is still silent. Asked as D6: owner chose "Warn me" (C6).
7. Low, R2 marked approved. Accepted: state is now "defaulted".
8. Low, stamp overwritten on re-export. Asked as D7: owner chose "Keep the first date" (C7).
9. Low, non-PDF originals as is. Recorded in C3.
10. Low, fixtures use invalid hashes. Accepted: new tests use real sha256 hashes with files in originals/, plus an explicit test that an escaping stored_filename with a valid original is delivered as the original.
11. Low, stale text. Accepted: ExportPage "PDF + CSV" wording, spec zip layout line, one spelling of the date expression (`substr(..., 1, 10)`).

### R5: omission warning (D6)
State: approved. Actual answer: owner 2026-10-22 "Warn me (recommended)". Accepted scope: C6.

### R6: first export date (D7)
State: approved. Actual answer: owner 2026-10-22 "Keep the first date (recommended)". Accepted scope: C7.

Approval readiness: PASS (R1 "1. ok."; R2 defaulted, owner informed 2026-10-22; R3 "yes. agree."; R4 "status filter (google label)..." with reading confirmed by no correction; R5 D6; R6 D7; C1/C3 by TODO approval D9 in /ship #72).

## NOT in scope
- "#72 review follow-ups" (R1): next PR, before #73.
- Using `converted/` renderings: the original is the document.
- Issue Date periods that never move: needs a frozen period column; the owner exports by upload date.

## What already exists
`atomic.find_original` (originals lookup, hash check), `filing.download_name` (safe original names), `storage.get_filed_path` (filed containment), `_slugify` (folders), the `omitted` set and stamping in export.py. All reused.

## Failure modes
- Filed copy renamed mid-export: next candidate (original); test with a patched `zf.write`. Not silent.
- Original missing too: omitted, unstamped, C6 warning in the UI. Not silent.
- Malformed receipt_date: upload date (C2); test.
- No critical gaps.

## Worktree parallelization strategy
Sequential implementation, no parallelization opportunity.

## Implementation Tasks
- [ ] **T1 (P1, human: ~3h / CC: ~20min)**: backend/api/export.py: drop the status filter; C2 date rule; C3 candidate list with download_name; C5 column; C6 header; C7 COALESCE
- [ ] **T2 (P1, human: ~3h / CC: ~20min)**: tests/test_export.py: the GAP rows of Section 3 with real hashes; flip :144 second half; :344 split
- [ ] **T3 (P2, human: ~1h / CC: ~10min)**: frontend api.exportDocs returns the omitted count; ExportPage + DocumentsPage warning; wording
- [ ] **T4 (P2, human: ~30min / CC: ~5min)**: docs: CLAUDE.md, v1 spec export section, TODOS.md entry removed

## Completion summary
- Step 0: Scope Challenge: scope accepted as-is
- Reality rows: 7/7 applicable rows emitted, 0 unverified premises
- Architecture Review: 2 issues found
- Code Quality Review: 2 issues found
- Test Review: diagram produced, 11 gaps identified
- Performance Review: 1 issue found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 0 items proposed (the follow-ups entry already exists)
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0
- Outside voice: codex not installed; native Claude Plan fallback completed (11 findings)
- Parallelization: 1 lane, 0 parallel / 1 sequential
- Lake Score: 4/4

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | codex (not installed), native Claude Plan fallback | Independent 2nd opinion | 1 | unavailable (native fallback completed) | 11 findings, all dispositioned |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 16 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex plan-review phase unavailable (CLI not installed); native in-host fallback completed, 11 findings, not counted as outside coverage.
- **VERDICT:** no review CLEAR; eng review findings are mapped work (T1-T4), all decisions answered. Ready to implement.

NO UNRESOLVED DECISIONS
