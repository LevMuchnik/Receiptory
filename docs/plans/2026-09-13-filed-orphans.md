# Issue #54 — reprocessing orphans the old `filed/` copy

## The bug in one paragraph

`generate_stored_filename` builds the `filed/` name out of `receipt_date` and
`vendor_receipt_id`, both verbatim LLM output. `save_filed` has no exists-guard
and `pipeline.py:99` re-runs it on every reprocess. A reprocess that extracts a
different value writes a *new* name and leaves the old file behind forever.
Nothing ever looks for those files, so they accumulate silently and ship in
every one of the 15 retained backups.

Last night's run says it out loud:

```
2026-09-14 02:00:02  Backup verified: 314 documents, 314 originals hash-checked,
                     310 filed present, schema 9
```

310 filed present — and 315 files went into the archive.

## Measured on the live install (in the running container)

```
rows with stored_filename: 310    distinct: 310    missing from disk: 0
files on disk in filed/:   315    orphans: 5       1027 KB
storage/filed total:       74 MB  = 37% of the 200.7MB backup, x15 retained
```

### The issue's stated cause is wrong

#54 says orphans come from a changed **receipt id**. The receipt ids are stable.
Hashing every orphan against its live counterpart shows what actually moves:

| orphan (old name) | live name (current row) | differs by |
|---|---|---|
| `2024-04-03-1111803-df457b78` | `2026-04-03-1111803-df457b78` | **year only** |
| `2024-04-03-175842-a459fcc6` | `2025-04-03-175842-a459fcc6` | **year only** |
| `2026-04-07-3942236619-11033bb9` | `2024-04-07-3942236619-11033bb9` | **year only** |
| `2024-04-03-742-175842-a459fcc6` | `2025-04-03-175842-a459fcc6` | year + id |
| `0000-00-00-000000-e4b519a0` | **no row exists at all** | — |

All four with a live counterpart are **byte-identical to it** (SHA-256 verified,
not assumed — the `converted/` learning of 2026-09-12 exists for this reason).

Two findings fall out that outrank #54 and get their own issues (D14, D15):

1. **`receipt_date` year instability** on documents 15, 16 and 32, at
   `extraction_confidence` 0.95–0.98. `receipt_date` assigns a receipt to a tax
   period. At least one value in each pair is wrong. The orphan filenames are
   the only surviving record of the earlier extraction, which is why the issue
   is filed **before** the cleanup deletes them.
2. **A document with files and no row.** `e4b519a0` has an `originals/` copy and
   a `filed/` copy and no row — not soft-deleted, absent.
   `grep -rn "DELETE FROM documents" backend/ scripts/ migrations/` returns
   nothing. Ids run 1..314 with no gaps, which fits a restore to an earlier
   point while `storage/` kept the newer files.

## Facts that constrain the fix

- `documents.file_hash` is `UNIQUE NOT NULL` (`001_initial_schema.sql:68`). The
  8-hex suffix is only 32 bits, so the unlink checks rather than assumes.
- `delete_document` (`api/documents.py:287`) is a **soft** delete. 70 of 314
  rows are soft-deleted and still reference their file, so orphan detection must
  **not** filter on `is_deleted`.
- **Nothing in this codebase has ever deleted from `storage/`.** `runner.py:181`
  depends on that in writing. See Part 1.
- `backend/storage.py:7` imports `fitz` (PyMuPDF) at module level.
  `backend/backup/verify.py` imports only stdlib, deliberately — it runs on the
  restore path. This forces D13.
- Both the backup and the processing queue run in executor threads
  (`scheduler.py:189`, `queue.py:59`), so a plain `threading.Lock` blocks
  neither event loop.
- `filed/` is browsed directly on the NAS (D9). It stays. The "retire `filed/`"
  option is **rejected**, not deferred.

## Decisions

| # | Decision |
|---|---|
| D1 | One `get_filed_path` resolver; **all** call sites route through it, incl. `api/export.py` |
| D3 | Strict rule: `stored_filename` must be a plain filename. One shared predicate |
| D4 | Keep the cross-row check before unlinking |
| D5 | Delete the 4 proven duplicates |
| D6 | Orphans get their own count, **not** `problems` |
| D7 | Ordering test + call-site mutation battery |
| D8/D9 | `filed/` is browsed directly and stays. Retiring it is rejected, not filed |
| D10 | A shared `threading.Lock` interlocks the unlink against `build_backup` |
| D11 | **Leave `save_filed` at `pipeline.py:99`.** Accepted ceiling — see below |
| D12 | Move the rowless orphan to `data/quarantine/`, outside all backup trees |
| D13 | The predicate and the lock live in `backend/atomic.py` |
| D14/D15 | File the year-instability and rowless-document findings as issues — **#63** and **#64**, both filed |

## Part 0 — one resolver, four call sites (D1, D3, D13)

Four places join `storage/filed/` with a database-supplied name. **None is
guarded**, and `api/export.py:164` hands the result to `zf.write` as root, so a
row naming `/etc/shadow` is read into the export zip today (the arcname becomes
`expense/<cat>//etc/shadow`):

```
storage.py:50-52                 save_filed      os.path.join   (writes)
api/export.py:164                export zip      os.path.join   (reads, as root)  <- open hole
verify.py:226                    verify_backup   os.path.join   (guarded upstream)
scripts/compare_json_mode.py:69  dev harness     os.path.join
```

PR #60's learning (10/10) says the guard belongs in the resolver "so the
invariant does not depend on how the row arrived".

```python
# backend/atomic.py — the stdlib-only leaf both sides already import.
def is_contained_name(name: str) -> bool:
    """True if `name` is a plain filename that cannot escape its directory."""
    return bool(name) and not os.path.isabs(name) and os.path.basename(name) == name

# backend/storage.py
def get_filed_path(stored_filename: str, data_dir: str) -> str:
    if not is_contained_name(stored_filename):
        raise ValueError(f"stored_filename escapes the filed directory: {stored_filename!r}")
    return os.path.join(data_dir, "storage", "filed", stored_filename)
```

The predicate goes in `atomic.py`, **not** `storage.py`: `verify.py` must import
it, and `storage.py` would drag PyMuPDF onto the restore path (D13).

**Strict, not realpath (D3).** The looser realpath rule used by
`get_scanner_test_frame_path` accepts `sub/dir/x.pdf`, which `verify.py` treats
as fatal. Two guards on one column that disagree is worse than one guard,
because the disagreement only surfaces the night a backup refuses to verify.

## Part 1 — stop making new orphans (D1, D4, D10, D11)

```
save_filed(new) ──► UPDATE SET stored_filename = new ──► [LOCK] unlink(old)
      │                        │                              │
  crash here:              crash here:                    crash here:
  new is a spare,          new is a spare,                nothing left
  row names old            row names new                  to go wrong

  every crash point leaves an ORPHAN — today's behaviour
  the reverse order would leave a row naming a DELETED file
```

After the row UPDATE commits:

1. Skip unless `previous` exists and `previous != stored_filename`.
2. Skip if any other row references `previous` (D4).
3. Acquire the storage-mutation lock (D10), then `storage.remove_filed`.

`ValueError` and `OSError` are both logged and swallowed at the call site.
Filing has succeeded and the row is already correct, so a stale copy that cannot
be removed must never fail the document.

### The interlock is the load-bearing part (D10)

`build_backup` snapshots the database, then `copytree`s `storage/` while the app
runs. Its safety argument is written at `runner.py:181-186`:

> every row in the snapshot already had its bytes on disk when the snapshot was
> taken, **and those bytes are still there when the copy runs**

That second clause has been free because nothing has ever deleted from
`storage/`. **Part 1 is the first code that falsifies it.** Two failure modes:

- Unlink between snapshot and walk → the snapshot row names a deleted file →
  `verify_backup` reports damage on a healthy install.
- Unlink *during* the walk → `_copy_tolerating_rename` (`runner.py:139-157`)
  retries once with `shutil.copy2`; for a genuinely deleted file the retry also
  raises → `shutil.Error` at the end of the walk → `build_backup`'s
  `except BaseException: rmtree(backup_dir); raise` → **no backup that night.**

So a module-level `threading.Lock` in `atomic.py`. `build_backup` holds it
across snapshot **and** copytree (2.7s, measured on run 170). `remove_filed`'s
caller acquires it. Both are already in executor threads, so neither blocks an
event loop. Add a reciprocal comment at `runner.py:181` pointing at the lock, so
the invariant stops being true by luck.

### Accepted ceiling: the 18-line window stays open (D11)

`save_filed` is at `pipeline.py:99`; the UPDATE is at `:117`. Between them sit
two category queries, `json.dumps` over LLM data, and `estimate_cost` →
`import litellm`. A raise in there hits the handler at `:24-27`, which sets
`status='failed'` without touching `stored_filename` — so the **new** file is
orphaned permanently and the unlink never runs.

This is **not fixed**, by decision. Part 1 therefore stops orphans from
*successful* reprocesses only, and Part 2 will report the rest. Marked in code:

```python
# gstack-shortcut(dec-333b80f5): save_filed stays 18 lines above the UPDATE, so a
# reprocess that raises in between orphans the file it just wrote. Upgrade when
# filed_orphans climbs without a matching successful rename.
```

## Part 2 — report orphans (D6)

Walk the backup's `storage/filed/`, subtract every `stored_filename` in the
database (soft-deleted rows included), and report the remainder as **its own
count** — never in `problems`.

`problems` means "this document's bytes are missing or do not match their hash"
and drives the damage notification. An orphan is the opposite fact. Folding it
in would report damage on a healthy install forever, which is the #48
verifier-must-not-become-its-own-outage rule in its noise form.

- report gains `filed_orphans: int` and `orphan_names: list[str]` (first `_MAX_LISTED`)
- `format_report` gains `", N unreferenced"` when non-zero
- never fatal, whatever the count
- **no `TMP_PREFIX` skip.** `_ignore_regenerable` (`runner.py:111-133`) already
  strips those names during `copytree`, so a backup cannot contain one. A skip
  here would be a branch that cannot execute — the same dead-layer pattern
  rejected in D3.

## Part 3 — the 5 files on disk (D5, D12)

Delete the four hash-verified duplicates. **Move** `0000-00-00-000000-e4b519a0.pdf`
to `data/quarantine/` — outside all three backup trees — so the artifact
survives for D15 while the orphan count can settle at zero. A count that is
permanently 1 can never mean "something new happened", which is the only thing
such a count is for.

One-off script, run on the host against the live data dir, printing each file
and its verified-identical counterpart before touching anything. `scripts/` is
not in the Docker image (logged 10/10), so the script says so in its docstring.

## Test plan (D7)

Behaviour, all 24 gaps from the coverage diagram: `get_filed_path` accept plus
three reject shapes; `remove_filed` present / absent / bad name; reprocess with
a changed year, with no change, on first process, with a traversal name, with a
name another row references, with `os.unlink` raising; verify counting an
orphan, counting zero, ignoring a soft-deleted row's file, surviving an absent
`filed/`; export skipping a traversal row and still building the zip.

**Harness note:** existing pipeline tests set `mock_extract.return_value`, a
single fixed result. A changed-year reprocess needs
`side_effect=[first, second]` — `return_value` twice exercises the no-change
branch only and would pass while mutant 7 lives.

**Ordering test.** Spy on the unlink; assert the row already carries the new
name at that moment. Reversing the two statements passes every other test.

**Lock test.** Assert `build_backup` holds the lock while copying: take it in a
second thread and prove the copy waits, or spy on acquire/release order. Without
this the lock is decoration.

**Mutation battery — call sites first** (the 10/10 learning; last PR's battery
scored 17/17 while all four call sites could be reverted with the suite green):

| # | Mutant | Must fail |
|---|---|---|
| 1 | `save_filed` → bare `os.path.join` | resolver test |
| 2 | `export.py:164` → bare `os.path.join` | export traversal test |
| 3 | `remove_filed` → bare `os.path.join` | remove bad-name test |
| 4 | delete the pipeline unlink block | reprocess test |
| 5 | `verify.py` → local copy of the predicate | shared-predicate test |
| 6 | swap UPDATE and unlink | ordering test |
| 7 | drop the `previous != new` guard | no-change reprocess test |
| 8 | drop the cross-row guard | D4 branch test |
| 9 | orphans appended to `problems` | D6 grade test |
| 10 | `build_backup` does not take the lock | lock test |

**Dropped from the battery, honestly:** mutating `compare_json_mode.py:69` was
in the draft. `grep -rn compare_json_mode tests/` is empty — no test imports that
script, so the mutant would survive and the battery would repeat the exact
lesson it exists to honour. The script does route through the resolver; nothing
enforces that it keeps doing so.

(An earlier revision of this line claimed the routing was already in place when
the edit had never been made. Caught at ship time by grepping the four call
sites the table above lists, instead of trusting the prose.)

## NOT in scope

- **Retiring `filed/`** — **rejected**, not deferred (D9). It is browsed
  directly on the NAS, which no amount of code reading could have established.
- **`receipt_date` year instability** — own issue (D14). More important than
  #54, different system.
- **The rowless document** — own issue (D15). Evidence quarantined, not deleted.
- **Moving `save_filed`** — declined (D11), ceiling recorded as dec-333b80f5.
- **#62 `_find_original` O(n²)** — measured: `verify_backup` runs in 0.13s over
  314 documents including SHA-256 of all 80MB of `originals/`. The glob costs
  roughly 20ms. Premature, and an unrelated change in this diff.
- **Bulk re-extraction of the affected documents** — standing constraint: fixes
  apply from now on.
- **Issue #46** (staging dirs never cleaned) — confirmed live during this
  review: run 170 left a 193MB `/tmp` directory after **succeeding**. Already
  filed; not this PR.

## What already exists

| Existing | Reused how |
|---|---|
| `get_scanner_test_frame_path` (`storage.py:145`) | Shape of the resolver only; D3 picks the stricter rule |
| `_is_contained_name` (`verify.py:64`) | Promoted to `atomic.is_contained_name`, the single definition |
| `verify_backup`'s existing row read | Orphan scan is one `listdir` plus a set subtraction |
| `_copy_tolerating_rename` (`runner.py:139`) | Explains why a delete needs a lock and not a retry |
| `test_pipeline.py`'s `extract_document` patch | Reprocess tests need no new harness |

## Failure modes

| New path | Realistic failure | Test? | Handled? | Silent? |
|---|---|---|---|---|
| `remove_filed` | file already gone | yes | tolerated | by design |
| `remove_filed` | `OSError` (permissions, read-only) | yes | logged, document completes | no |
| `get_filed_path` | restored row names `/etc/shadow` | yes | `ValueError`, caller decides | no |
| unlink vs backup | delete during `copytree` | yes | **the lock** | no |
| unlink vs backup | delete between snapshot and walk | yes | **the lock** | no |
| pipeline | crash between UPDATE and unlink | n/a | leaves an orphan, Part 2 reports it | no |
| pipeline | raise between `:99` and `:117` | n/a | **accepted ceiling, dec-333b80f5** | reported by Part 2 |
| orphan scan | `filed/` absent in an old backup | yes | count 0 | n/a |
| export | traversal row | yes | skipped, zip still built | no |

No critical gaps: every path has a test and error handling, and the one accepted
ceiling is reported by Part 2 rather than being silent.

## Parallelization

Sequential. Every step routes through `atomic.py` and the one new resolver, so
splitting across worktrees would collide.

## Implementation Tasks

- [x] **T1 (P1, human: ~40min / CC: ~10min)** — atomic.py — Add `is_contained_name` and the storage-mutation `threading.Lock`
  - Surfaced by: D13 — `storage.py:7` imports `fitz`; `verify.py` is stdlib-only by design
  - Files: `backend/atomic.py`, `CLAUDE.md`
  - Verify: `uv run pytest tests/test_atomic.py`
- [x] **T2 (P1, human: ~1h / CC: ~15min)** — storage — Add `get_filed_path` / `remove_filed`; route `save_filed` through them
  - Surfaced by: D1 — four unguarded joins of a DB-supplied name
  - Files: `backend/storage.py`, `tests/test_storage.py`
  - Verify: `uv run pytest tests/test_storage.py`
- [x] **T3 (P1, human: ~30min / CC: ~8min)** — export — Route `export.py:164` through the resolver, skip and log rows that raise
  - Surfaced by: D1 — live arbitrary root read into the export zip
  - Files: `backend/api/export.py`, `tests/test_export.py`
  - Verify: `uv run pytest tests/test_export.py`
- [x] **T4 (P1, human: ~3h / CC: ~25min)** — backup — Hold the lock across snapshot + copytree; reciprocal comment at `runner.py:181`
  - Surfaced by: outside voice finding 1 — the unlink falsifies a documented invariant
  - Files: `backend/backup/runner.py`, `tests/test_backup.py`
  - Verify: `uv run pytest tests/test_backup.py`
- [x] **T5 (P1, human: ~2h / CC: ~20min)** — pipeline — Post-UPDATE unlink under the lock, with the `previous != new` and cross-row guards
  - Surfaced by: D4, D10, D11 — plus the dec-333b80f5 shortcut marker
  - Files: `backend/processing/pipeline.py`, `tests/test_pipeline.py`
  - Verify: `uv run pytest tests/test_pipeline.py`
- [x] **T6 (P2, human: ~1.5h / CC: ~20min)** — verify — Orphan scan, `filed_orphans`, `format_report` clause; import the shared predicate
  - Surfaced by: D6 — `problems` must keep meaning bytes-missing
  - Files: `backend/backup/verify.py`, `tests/test_backup.py`
  - Verify: `uv run pytest tests/test_backup.py`
- [x] **T7 (P2, human: ~1h / CC: ~15min)** — scripts — One-off cleanup: delete 4 duplicates, move the 5th to `data/quarantine/`
  - Surfaced by: D5, D12
  - Files: `scripts/cleanup_filed_orphans.py`
  - Verify: dry run on live data confirms 4 DUPLICATE + 1 UNMATCHED, hashes printed.
    **Script written; --apply deliberately NOT run yet** — the cleanup belongs at
    deploy time, after the code that stops new orphans is live.
- [x] **T8 (P2, human: ~2h / CC: ~25min)** — tests — Ordering test, lock test, 10-mutant call-site battery
  - Surfaced by: D7 — last PR's battery was hollow
  - Files: `tests/test_pipeline.py`, `tests/test_backup.py`
  - Verify: each mutant produces at least one failure
- [x] **T9 (P3, human: ~30min / CC: ~8min)** — issues — File D14 (year instability) and D15 (rowless document)
  - Surfaced by: D14, D15 — D14's evidence is deleted by T7, so this lands first
  - Files: —
  - Verify: `gh issue list` — **done: #63 and #64 filed 2026-09-14**

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 9 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |
| Outside Voice | subagent | Independent plan challenge | 1 | issues_found | 8 findings, 5 absorbed, 1 reversed a decision |

**CROSS-MODEL:** The outside voice (Claude subagent, same model family — weigh
accordingly) found one P0 the four review sections missed: the unlink is the
first code in this repo ever to delete from `storage/`, falsifying the
snapshot-then-copy invariant documented at `runner.py:181-186`, which turns a
reprocess overlapping 02:00 into a failed backup run. Verified in the code and
folded as D10. It also caught that the orphan window is 18 lines rather than 3
(D11, declined with a recorded ceiling), that D3 would drag PyMuPDF onto the
restore path (D13), that D5 and D6 contradicted each other (D12), and that 2 of
10 mutants were hollow (dropped). Its strategic claim — that retiring `filed/`
makes the whole plan unnecessary — was escalated as D8 and settled by D9: the
directory is browsed directly on the NAS, which no code reading could establish.
Rejected, not deferred.

**VERDICT:** ENG CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
