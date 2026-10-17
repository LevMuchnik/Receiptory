# Issue #69: download the original file from the document page

**Status:** eng-reviewed (2026-10-12), implementing · **Branch:** `issue/69-download-original`

## Problem

The document page shows rendered page images but offers no way to get the file itself
as it was submitted (the phone scan, the emailed PDF, the Telegram photo).

What exists today:
- `GET /api/documents/{id}/file/{original|converted}` (`backend/api/documents.py`,
  `serve_file`) returns the bytes. **Nothing in the frontend calls it, and no test
  covers it.**
- It returns `FileResponse(path)` with no `Content-Disposition`, so a browser download
  is named after the URL's last segment: `original`, with no extension.
- `original_filename` is a poor download name: `scan_1791552248041.jpg`,
  `05e1ee4e43a5434fb4d442590eac84a7.pdf`, and on 14 rows (11 web uploads, 3 Telegram) a raw
  header value: `attachment; filename=0a6fef7c-….pdf`.
- The endpoint re-derives the stored extension as `splitext(original_filename)` or
  `.pdf`, while ingestion uses `.bin` as its fallback (`telegram.py:107,191`,
  `gmail.py:170,517`), takes the extension from a different string in two places
  (`telegram.py:107` uses Telegram's `file_path`, `gmail.py:348` the fetched file and
  does not lowercase it). All 376 rows resolve today (checked read-only, 2026-10-12),
  so this is latent, not live.

## Design

### 1. Backend: `GET /api/documents/{id}/download` (new) or a flag on `serve_file`

Returns the original bytes with `Content-Disposition: attachment` and a useful name.
`FileResponse(path, filename=...)` already emits RFC 5987 `filename*=utf-8''...`, so
a Hebrew vendor name survives.

- **Locating the file:** `get_file_path("original", ...)` first; if absent, the single
  `originals/<hash>.*` match (closes the latent extension mismatch for this endpoint).
  Zero or several matches → 404.
- **Download name:** see the open question below. The extension is always the stored
  file's own extension, never the display name's.
- **Media type:** from the stored extension (`mimetypes`), `application/octet-stream`
  for `.bin`.
- Auth: `require_auth`, like every documents route.

### 2. Frontend: "Download original" on `DocumentDetailPage`

A button in the header action row (next to Approve / Reprocess), rendered as a plain
`<a href=... download>` to the endpoint: the browser handles the save and the auth
cookie, no blob juggling, works on the phone. Material icon `download`.

### Open question for review: the download name

Candidates:
- the `filed/` name with the original extension (`2026-10-01-30009-ab12cd34.jpg`),
  the same scheme you see when browsing `storage/filed/`;
- `original_filename` cleaned up (strip the `attachment; filename=` junk);
- `"<vendor> <receipt_date>.<ext>"`.

## Not in scope

- The 14 `attachment; filename=...` names are not an ingestion bug: they arrived that way
  (11 web uploads on 2026-10-05 within two seconds, 3 Telegram, all `<uuid>.pdf`, most likely
  saved that way by one vendor portal). The D2 fallback strips the prefix; nothing else to fix.
  (Factual correction made during the eng review.)
- Changing ingestion's `.bin` / extension derivation.
- Downloading the converted PDF or the `filed/` copy (the request is for the original).

## Tests

- Download returns the exact original bytes, `Content-Disposition: attachment` with the
  agreed name, the right media type; Hebrew name encoded (RFC 5987).
- Fallback finds `originals/<hash>.bin` when the derived extension misses; 404 when
  absent; 404 for an unknown document; 401 without a session.
- No DOM tests (repo policy); the link's URL builder lives in `lib/api.ts`.

## Eng review (2026-10-12): Step 0 and findings

Target: this plan (named explicitly). Report file: this plan.
Prior learning applied: `promoting-a-tree-to-a-backup-promotes-its-path-column`
(10/10, 2026-09-13): a path column restored from a backup is untrusted input, and the
runtime resolver has to enforce the invariant, not only the restore path.

**Scope Challenge A**
- Existing code: `serve_file` (`backend/api/documents.py:344`) already serves the bytes;
  `verify._find_original` (`backend/backup/verify.py:117`) already locates
  `originals/<hash>` under any extension, including none; Starlette 1.6
  `FileResponse(filename=...)` already writes `Content-Disposition` with RFC 5987
  `filename*=utf-8''` for non-ASCII names [measured: installed source].
- Minimum: a filename on the existing response, a robust locator, one link in the page.
- Complexity: ~5 files (documents.py, verify.py or atomic.py, DocumentDetailPage.tsx,
  lib/api.ts, test_documents_api.py), 0 new classes. Below the gate; B skipped.
- Search: no new pattern; Starlette built-in covers the header. [Layer 1]
- TODOS: nothing related.
- Distribution: none.

REALITY: premise-table pass unverified=0 (route documents.py:344; no frontend caller, `grep -rn "/file/" frontend/src` empty; 14 junk names, 376/376 resolve, both [measured: read-only DB scan 2026-10-12])
REALITY: already-done pass no commits on origin/master since 2026-10-12 to documents.py or DocumentDetailPage.tsx; base head e396093
REALITY: surface-check pass GET /documents/{doc_id}/file/{file_type} exists (documents.py:344); FileResponse filename param verified in starlette 1.6.0
REALITY: binding-decisions pass gstack-decision-search --scope branch empty for this branch at e396093
REALITY: numbers pass 14 = 11 web_upload + 3 telegram; 376 rows; 1 live doc with no stored_filename [measured: read-only scan of data/receiptory.db at e396093] backend/api/documents.py:352
REALITY: deferred-asks pass issue #69 asks for one control with a useful name; nothing left out (plan Problem section, docs/plans/2026-10-12-issue69-download-original.md:5)
REALITY: pr-count pass one PR; .gstack/ship-policy.json absent at e396093

**Scope Challenge C findings**
- S1 [P2] (confidence: 9/10) plan "Open question for review: the download name": undecided. → D2.
- S2 [P2] (confidence: 9/10) `backend/api/documents.py:352` `ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"`
  disagrees with every writer: `api/upload.py:36` `ext = os.path.splitext(upload.filename or ".pdf")[1].lower()`
  (a name without a dot stores `originals/<hash>` with no extension), `telegram.py:107` /
  `gmail.py:170,517` fall back to `.bin`, `gmail.py:348` does not lowercase. `verify._find_original`
  already handles all of these. → D3.
- S3 [P1] (confidence: 8/10) `backend/storage.py:120` `return os.path.join(data_dir, "storage", "originals", f"{file_hash}{ext}")`
  joins a DB value unchecked; only the restore path validates it (`verify.py:56`
  `_HASH_RE = re.compile(r"[0-9a-f]{64}")`). A row with `file_hash = "../../../etc/x"`
  makes the download an arbitrary read, the same class as #45/#54. → D4.

## Decision ledger

### R0: Scope
feature answers: none (gate not tripped); structure: n/a; accepted scope: the plan as written; pending remedies: R1-R3.

### R1: Download filename (S1)
Finding: S1, P2, confidence 9/10, plan "Open question for review", reviewer: Claude (eng review)
Plan baseline: open question, three candidates listed
Runtime evidence: original names are often junk (`scan_1791552248041.jpg`, 14 `attachment; filename=...` rows); 303 of 304 live documents have a stored_filename; the filed name carries an LLM-derived date (issue #63: 31 wrong until repaired)
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 download name | `original` (URL segment) | filed name stem + original extension, e.g. `2026-10-01-30009-ab12cd34.jpg`; fallback cleaned original name, then `document-<id><ext>` | cleaned original_filename (strip `attachment; filename=`), fallback `document-<id><ext>` | `<vendor> <date>.<ext>`, sanitized; fallback as A |
| R2 locator | pending | pending | pending | pending |
| R3 hash check | pending | pending | pending | pending |
Question D2:
D2 — What should a downloaded original be called?
Project/branch/task: issue/69-download-original, the filename your browser saves.
ELI10: The file itself is always the exact original. Only the name is in question. Original names are mostly junk ("scan_1791552248041.jpg"). The filed/ copies you browse on the NAS are named date-receiptnumber-hash ("2026-10-01-30009-ab12cd34.pdf"). A download could use that same name with the original's extension, the cleaned-up original name, or "vendor date".
Stakes if we pick wrong: downloads pile up in your Downloads folder with names you can't match to receipts, or don't match what you see in filed/.
Recommendation: A because it matches the filed/ names you already use, is already filesystem-safe, and sorts by date.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Filed name (recommended)
  ✅ Same name as the filed/ copy on the NAS; sorts by date in any folder
  ✅ Already sanitized for filesystems by generate_stored_filename
  ❌ Carries the extracted date, so a #63-style wrong year shows in the name
B) Cleaned original name
  ✅ What the sender or scanner called it; no extracted data involved
  ❌ Mostly meaningless (scan_1791552248041.jpg, random hex from Telegram)
C) Vendor and date
  ✅ Most readable: "סידנא הסעדות 2026-05-26.pdf" says what it is
  ❌ Vendor text needs new sanitizing; two receipts from one vendor on a day collide
Net: consistency with filed/ versus readability versus untouched sender names.
Header: Download name
Options:
A) Filed name (recommended)
Name the download after stored_filename with the original file's extension (2026-10-01-30009-ab12cd34.jpg); when there is no stored_filename yet, the cleaned original name, else document-<id><ext>. CC ~10 min.
B) Cleaned original name
Use original_filename with header junk stripped (attachment; filename=...) and path parts removed; fallback document-<id><ext>. CC ~10 min.
C) Vendor and date
Build "<vendor> <receipt_date>.<ext>" with new sanitizing for slashes and control characters; fallback as A. CC ~20 min.

State: approved
Actual answer: A) Filed name (recommended), D2, 2026-10-12
Accepted scope: download name = stored_filename stem + the stored original's extension; no stored_filename → cleaned original_filename (header junk and path parts stripped); else document-<id><ext>
History: none

### R2: Locating the original on disk (S2)
Finding: S2, P2, confidence 9/10, backend/api/documents.py:352 vs api/upload.py:36, telegram.py:107, gmail.py:170/348/517, reviewer: Claude (eng review)
Plan baseline: original proposal: derived path first, else the single `originals/<hash>.*` match (plan §1 "Locating the file")
Runtime evidence: 376/376 rows resolve by the derived path today [measured]; `verify._find_original` (verify.py:117) handles the no-extension file the plan's glob would miss
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 download name | approved A (D2) | same | same | same |
| R2 locator | ext derived from original_filename, `.pdf` fallback | move `_find_original` to `backend/atomic.py` as `find_original`; verify.py and the download both call it | plan's fallback written inside documents.py (derived path, then `<hash>.*`) | unchanged derived path |
| R3 hash check | pending | pending | pending | pending |
Question D3:
D3 — How should the download find the original file on disk?
Project/branch/task: issue/69-download-original, locating originals/<hash><ext>.
ELI10: Originals are stored as their hash plus an extension, but four ingestion paths pick that extension four different ways (.pdf, .bin, none at all, upper case). The page endpoint guesses one way. Every file resolves today, so nothing is broken yet. The backup checker already has a small function that finds the file whatever its extension; the download could share it instead of guessing.
Stakes if we pick wrong: a future upload with an odd name shows a download button that returns "File not found".
Recommendation: A because one shared, already-tested lookup beats a second copy that misses the no-extension case.
Completeness: A=10/10, B=7/10, C=3/10
Pros / cons:
A) Share the backup lookup (recommended)
  ✅ One function finds every original, including names with no extension
  ✅ Already exercised by the nightly backup verify on all 376 files
  ❌ Moves a private helper into atomic.py, a file with a mixed purpose
B) Fallback in the endpoint
  ✅ Change stays inside documents.py; nothing else moves
  ❌ Second copy of the lookup, and it misses originals/<hash> with no dot
C) Keep the guess
  ✅ No code change to the lookup at all
  ❌ Any upload stored as .bin or without extension downloads as a 404
Net: one shared lookup for all cases versus a local copy versus the known gap.
Header: File lookup
Options:
A) Share the backup lookup (recommended)
Move verify._find_original to backend/atomic.py as find_original(storage_dir, file_hash) (stdlib-only, like is_contained_name); verify.py imports it; the download uses it and takes the extension from the file it finds. CC ~10 min + tests.
B) Fallback in the endpoint
Write the plan's fallback inside documents.py: derived path, then the single <hash>.* match. Misses the no-extension case unless that is added too. CC ~10 min.
C) Keep the guess
Leave the derived-extension lookup as it is; odd extensions stay a latent 404. CC none.

State: approved
Actual answer: A) Share the backup lookup (recommended), D3, 2026-10-12
Accepted scope: move verify._find_original to backend/atomic.py as find_original(storage_dir, file_hash), stdlib-only; verify.py imports it; the download locates the original with it and takes the extension from the file found; tests
History: none

### R3: Validate file_hash before building a path (S3)
Finding: S3, P1, confidence 8/10, backend/storage.py:120 `return os.path.join(data_dir, "storage", "originals", f"{file_hash}{ext}")`; also documents.py:373 and notifier.py:75 join `converted/{file_hash}.pdf` by hand. Reviewer: Claude (eng review). Prior learning: promoting-a-tree-to-a-backup-promotes-its-path-column
Plan baseline: none (the plan does not mention it)
Runtime evidence: only verify.py checks the shape (`_HASH_RE = re.compile(r"[0-9a-f]{64}")`, verify.py:56), on the restore path; 376/376 live hashes are valid [measured]; no exploit exists without a tampered database or backup
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 download name | approved A (D2) | same | same | same |
| R2 locator | approved A (D3) | same | same | same |
| R3 hash validation | restore path only | in find_original only (download + verify) | in find_original AND get_file_path, and the two hand-built converted/ paths (documents.py:373, notifier.py:75) go through get_file_path; a bad hash raises ValueError → 404 | none |
Question D4:
D4 — Should file paths refuse a malformed file hash?
Project/branch/task: issue/69-download-original, the download reads a file named after a database value.
ELI10: Each stored file is named by its hash, which comes from the database. A restored or tampered database row with a hash like "../../etc/passwd" would make the new download button hand out any file on the NAS. Today only the restore checker rejects such rows. A one-line shape check (64 hex characters) can refuse them wherever a path is built. No real row is affected: all 376 hashes are valid.
Stakes if we pick wrong: a bad row turns "download original" into "download any file the app can read".
Recommendation: B because one check in the shared path builder covers the download, the page images and notifications at once.
Completeness: A=7/10, B=10/10, C=3/10
Pros / cons:
A) Check in the new lookup only
  ✅ Closes the hole for the download this PR adds
  ❌ Page images and notifications still build paths from an unchecked hash
B) Check every path builder (recommended)
  ✅ One rule in get_file_path covers download, pages and notifications
  ✅ Removes two hand-built converted/ paths, so nothing bypasses the check
  ❌ Touches notifier.py and the page endpoint, a slightly wider diff
C) No check
  ✅ Smallest change; no real row is malformed today
  ❌ The new button becomes an arbitrary-file read after a bad restore
Net: one shared guard for every path versus guarding only the new door.
Header: Hash check
Options:
A) Check in the new lookup only
find_original returns None for a hash that is not 64 lowercase hex; the download answers 404. Other path builders unchanged. CC ~5 min + test.
B) Check every path builder (recommended)
find_original and get_file_path both reject a hash that is not 64 lowercase hex (ValueError → 404); documents.py:373 and notifier.py:75 switch to get_file_path("converted", ...). CC ~15 min + tests.
C) No check
Build paths from file_hash as today. CC none.

State: approved
Actual answer: A) Check in the new lookup only, D4, 2026-10-12 (not the recommended option)
Accepted scope: find_original returns None for a hash that is not 64 lowercase hex; the download answers 404; get_file_path, documents.py:373 and notifier.py:75 unchanged. Shortcut trail: Completeness 7/10 on a durable security-scope call, logged with gstack-decision-log and marked in code with a gstack-shortcut comment at get_file_path
History: none

### R4: Which endpoint serves the download (A1)
Finding: A1, P3, confidence 9/10, plan §1 "`GET /api/documents/{id}/download` (new) or a flag on `serve_file`", reviewer: Claude (eng review)
Plan baseline: open: new route or a flag on serve_file
Runtime evidence: serve_file (documents.py:344) has no caller in frontend/src and no test; `file_type` also accepts "converted"
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| R1-R3 | approved (D2-D4) | same | same |
| R4 endpoint | /file/{original,converted}, inline, no name | /file/original becomes the download (attachment, D2 name, find_original); /file/converted unchanged | new /documents/{id}/download; /file/ untouched, so two routes serve originals |
Question D5:
D5 — Reuse the existing file endpoint for the download, or add a new one?
Project/branch/task: issue/69-download-original, the URL behind the button.
ELI10: There is already an unused endpoint that returns the original file, just without a proper filename. We can upgrade it into the download, or add a separate /download address and leave the old one as it is. Nothing calls the old one today.
Stakes if we pick wrong: two addresses that serve the same file and can drift apart (one guarded, one not).
Recommendation: A because nothing uses the old endpoint, so upgrading it leaves exactly one way to fetch an original.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Upgrade /file/original (recommended)
  ✅ One endpoint serves originals; the hash check and lookup live in one place
  ✅ Smallest diff: no new route, the old unused code gets tests at last
  ❌ The URL says "file", not "download", which reads slightly less obvious
B) New /download route
  ✅ Self-describing URL; the old endpoint keeps its exact behaviour
  ❌ Two routes return the original; the old one stays unguarded and untested
Net: one guarded endpoint versus a clearer URL plus a leftover unguarded twin.
Header: Endpoint
Options:
A) Upgrade /file/original (recommended)
GET /documents/{id}/file/original returns the original as an attachment with the D2 name, located by find_original (D3/D4); /file/converted keeps its current behaviour. CC ~10 min + tests.
B) New /download route
Add GET /documents/{id}/download with the D2 name and find_original; leave /file/{original,converted} as is. CC ~10 min + tests.

State: approved
Actual answer: A) Upgrade /file/original (recommended), D5, 2026-10-12
Accepted scope: GET /documents/{id}/file/original returns the original as an attachment with the D2 name, located by find_original (D3/D4); /file/converted keeps its current behaviour; tests for both
History: none

## Eng review: sections 1-4

**1. Architecture** (findings A1 → D5, S2 → D3, S3 → D4 above)
- A2 (confidence: 9/10) The frontend link is a plain `<a href download>`: the session cookie
  rides along (same origin in production; in dev the API is `localhost:8484`, and cookies
  are not port-scoped), and `Content-Disposition: attachment` makes phones save rather
  than navigate. No blob or fetch code. Necessary implementation of D5; no question.

**2. Code quality**
- Q1 (confidence: 9/10) The name logic is a pure helper (`download_name(stored_filename,
  original_filename, doc_id, ext)`) beside `generate_stored_filename` in
  `processing/filing.py`, so it is unit-testable without a request. Implementation of D2.
- Q2 (confidence: 9/10) `verify.py` must keep importing without PyMuPDF
  (`test_atomic.py::test_this_module_stays_importable_without_pymupdf`): `find_original`
  goes to `atomic.py`, which already hosts `is_contained_name` for the same reason. D3.
- Q3 (confidence: 8/10) CLAUDE.md "Project Structure" line for `atomic.py` lists what it
  hosts and why; add `find_original`. Same commit.

**3. Tests** (pytest per CLAUDE.md `## Testing`; no DOM tests per repo policy)

```
CODE PATHS                                              USER FLOWS
[+] atomic.find_original (moved from verify.py)           [+] Owner taps "Download original"
  ├── [★★★ TESTED → keep] no-extension original             ├── [GAP] scan (.jpg) saves as <filed stem>.jpg
  │        test_backup.py:1193                              ├── [GAP] emailed PDF saves as <filed stem>.pdf
  ├── [★★ TESTED → keep] <hash>.<ext> via verify tests       ├── [GAP] pending/failed doc (no filed name) →
  ├── [GAP] malformed hash → None (D4)                       │        cleaned original name
  └── [GAP] nothing on disk → None                           └── [GAP] not logged in → 401
[+] filing.download_name                                  [+] Error states
  ├── [GAP] stored_filename + ext                            ├── [GAP] file missing on disk → 404
  ├── [GAP] no stored_filename → cleaned original_filename   └── [GAP] unknown document → 404
  │        (strip "attachment; filename=", path parts)
  ├── [GAP] both unusable → document-<id><ext>
  └── [GAP] Hebrew original name → RFC 5987 header
[+] api/documents.serve_file
  ├── [GAP] original: attachment, D2 name, exact bytes, media type
  ├── [GAP] original under .bin / no extension (D3)
  ├── [GAP] converted: unchanged behaviour (regression)
  └── [GAP] invalid file_type → 400 (existing branch, untested)

COVERAGE: 2/17 paths tested today  |  GAPS: 15 (0 E2E, 0 eval)
```

Regression: the `_find_original` move puts `verify_backup` at risk; existing coverage
(`test_backup.py:1193` and the verify suite) is the contract and must pass unchanged.
`/file/converted` keeps its behaviour, pinned by a new test. No LLM or prompt change,
so no eval.

**4. Performance:** No issues found. One stat or glob per download; FileResponse streams.

**Failure modes**
| Path | Realistic failure | Test | Handling | User sees |
|---|---|---|---|---|
| download | original deleted from disk | planned | 404 | browser error page "File not found on disk" |
| download | malformed hash in a restored row | planned (D4) | 404 | same |
| download | #63 wrong year | n/a | name follows the stored date | name shows the wrong year until repaired |
No critical gaps.

**NOT in scope**
- Guarding get_file_path / pages / notifier against a malformed hash (D4 chose A; shortcut logged, decision 85a652a8).
- Downloading the converted PDF or the filed/ copy.

**What already exists:** serve_file, verify._find_original, Starlette FileResponse
filename handling, generate_stored_filename. All reused; nothing rebuilt.

**Worktree parallelization:** Sequential implementation, no parallelization opportunity.

### R5: TODO candidate, one extension convention for originals
Finding: T1, P2, confidence 9/10, backend/processing/pipeline.py:59 `original_ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"` vs api/upload.py:36 (no extension stored for a dotless name), telegram.py:107 / gmail.py:170,517 (`.bin`), gmail.py:348 (no lowercase); notifier.py:70 repeats the pipeline's guess. Reviewer: Claude (eng review)
Plan baseline: none (out of the plan's scope; D3 fixed only the download)
Runtime evidence: 376/376 rows resolve today [measured]; a dotless upload ("receipt") would be stored as originals/<hash> and the pipeline would look for <hash>.pdf and fail the document
Comparison grid:
| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1-R4 | approved (D2-D5) | same | same | same |
| R5 extension mismatch in pipeline/notifier/pages | latent | TODOS.md entry (P2) | not recorded | built now: pipeline, notifier and serve_page locate originals with find_original |
Question D6:
D6 — Record a TODO for the mismatched file extensions?
Project/branch/task: issue/69-download-original, a latent bug found while reviewing the file lookup.
ELI10: The download now finds originals whatever their extension (D3). But processing, notifications and page images still guess the extension from the file's name, and four upload paths save it four different ways. Every file works today. A file uploaded without a dot in its name would be saved without an extension and then fail processing with "file not found".
Stakes if we pick wrong: one oddly named upload fails processing and you see it only as a failed document.
Recommendation: A because it is latent (0 of 376 affected) and the fix touches the processing pipeline, which deserves its own small issue.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Add to TODOS.md (recommended)
  ✅ The bug and its one-line fix (use find_original) are written down
  ✅ Keeps this PR to the download; pipeline changes get their own review
  ❌ The latent failure stays possible until the TODO is picked up
B) Skip
  ✅ TODOS.md stays focused on things already hurting
  ❌ The next person meets it as a mysterious failed document
C) Build it now
  ✅ Closes the bug everywhere in this PR, using the lookup D3 just shared
  ❌ Widens a UI feature PR into the processing pipeline and notifications
Net: write it down now versus fixing three more call sites in this PR.
Header: Ext TODO
Options:
A) Add to TODOS.md (recommended)
Add a P2 TODO "Locate originals with find_original everywhere" naming pipeline.py:59, notifier.py:70, documents.py:serve_page and the four ingestion conventions. No code now.
B) Skip
Record nothing beyond this plan.
C) Build it now
Switch pipeline.py, notifier.py and serve_page to find_original in this PR, with tests for a dotless and a .bin original. CC ~20 min.

State: approved
Actual answer: A) Add to TODOS.md (recommended), D6, 2026-10-12
Accepted scope: P2 TODO "Locate originals with find_original everywhere" added to TODOS.md (Ingestion); no code in this PR
History: none

Approval readiness: PASS (R1 ← D2 A, R2 ← D3 A, R3 ← D4 A, R4 ← D5 A, R5 ← D6 A, all 2026-10-12; regression contract = existing verify tests, no new behaviour change to verify_backup)

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific
finding above. Run with Claude Code or Codex; checkbox as you ship.

- [x] **T1 (P1, human: ~1h / CC: ~10min)** — atomic — move `_find_original` to `atomic.find_original`, reject a non-hex hash
  - Surfaced by: Scope Challenge S2/S3 (D3 A, D4 A)
  - Files: backend/atomic.py, backend/backup/verify.py, tests/test_atomic.py
  - Verify: `uv run pytest tests/test_atomic.py tests/test_backup.py -q` (test_backup.py:1193 unchanged)
- [x] **T2 (P1, human: ~1h / CC: ~10min)** — processing/filing — `download_name()` with the D2 fallbacks
  - Surfaced by: S1 (D2 A)
  - Files: backend/processing/filing.py, tests/test_filing.py
  - Verify: `uv run pytest tests/test_filing.py -q`
- [x] **T3 (P1, human: ~2h / CC: ~15min)** — api/documents — `/file/original` as an attachment, located by find_original; `/file/converted` unchanged
  - Surfaced by: A1 (D5 A)
  - Files: backend/api/documents.py, tests/test_documents_api.py
  - Verify: `uv run pytest tests/test_documents_api.py -q`
- [x] **T4 (P1, human: ~1h / CC: ~10min)** — frontend — "Download original" link on DocumentDetailPage, URL builder in lib/api.ts
  - Surfaced by: A2
  - Files: frontend/src/pages/DocumentDetailPage.tsx, frontend/src/lib/api.ts
  - Verify: `cd frontend && npm run build && npm test`; on the phone after deploy
- [x] **T5 (P2, human: ~10min / CC: ~2min)** — storage — `gstack-shortcut(dec-85a652a8)` marker at get_file_path (D4 A trail)
  - Surfaced by: S3 (D4 A)
  - Files: backend/storage.py
  - Verify: grep for the marker
- [x] **T6 (P2, human: ~15min / CC: ~3min)** — docs — CLAUDE.md atomic.py line names find_original
  - Surfaced by: Q3
  - Files: CLAUDE.md
  - Verify: read
_No new tasks from Performance._

## Completion summary
- Step 0: Scope Challenge — scope accepted as-is
- Reality rows: 7/7 applicable rows emitted, 0 unverified premises
- Architecture Review: 2 issues found (A1 → D5, A2 implementation note)
- Code Quality Review: 3 issues found (Q1-Q3, implementation of approved rows)
- Test Review: diagram produced, 15 gaps identified
- Performance Review: 0 issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 1 item proposed to user (accepted: "Locate originals with find_original everywhere", P2)
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: codex, unavailable (CLI not installed; native fallback needs a blocking task wait this session lacks)
- Parallelization: 1 lane, 0 parallel / 1 sequential
- Lake Score: 1/2 (D3 picked 10/10; D4 picked 7/10)

## Suppressed findings
- none

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | codex (`/plan-eng-review` outside voice) | Independent 2nd opinion | 1 | unavailable | CLI not installed; no native fallback (no blocking task wait) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES OPEN (PLAN) | 20 issues, 0 critical gaps; all resolved (D2-D6), 15 are planned test gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex, plan-review phase, unavailable, no findings. Missing coverage, not a clean pass.
- **VERDICT:** no review CLEAR; eng review findings are mapped to tasks T1-T6 and every decision is answered. Ready to implement; eng review required status stays "issues_open" by its counting rule.

NO UNRESOLVED DECISIONS
