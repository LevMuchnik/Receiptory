# Issue #67: request a JSON schema, not bare JSON mode

**Status:** implemented; gate run 2 PASS and /review fixes applied 2026-09-23 · **Branch:** `issue/67-structured-output` · **Unblocks:** #63
**Subsumes TODOs:** "Typed response_schema structured output for extraction", "Harden url_triage JSON parsing"

## Problem

**The goal is #63's foundation.** 28 of 251 dated documents (11%) carry the wrong year,
by two mechanisms: a printed `DD/MM/YY` read as `YY/MM/DD`, and a wrong year attached to
the right day and month. 23 of the 28 have already been exported. #63's fix needs
the model to transcribe the date exactly as printed (`receipt_date_as_printed:
"19/09/26"`), so code can resolve it. A new field the model must always return needs a
schema. Today the fields exist only as prose in the prompt (`extract.py:82-111`) and as
the `_EXPECTED_KEYS` shape gate (`extract.py:187`), and a prose request can be skipped.

**Supporting evidence.** Extraction sends `response_format={"type": "json_object"}`
(`extract.py:446`), which asks for "some JSON" and binds neither field names nor types. On the
production model (`gemini/gemini-3.5-flash`), 2026-09-17 → 09-22, two replies came back as
invalid JSON (`Expecting ',' delimiter`). Doc 330 recovered on the in-call retry. Doc 328
exhausted both attempts, went to `failed`, and processed only on the queue's second pass.
Raising `llm_parse_retries` would absorb those two failures, but it would not give #63 its field.

The three ingestion triage calls (`url_triage.py:65/139/224`) send no `response_format`
at all, and have drifted apart (see §3). This is hardening, not a measured failure.

## Evidence it works

- `litellm.supports_response_schema("gemini/gemini-3.5-flash") is True`; litellm 1.93
  routes Gemini 2.0+ to `responseJsonSchema` (standard JSON Schema, keeps
  `additionalProperties`, strips `strict`).
- Gemini documents support for `type: [..., "null"]`, `enum`, `required`,
  `additionalProperties`, and warns that "enums with many values" can fail with
  `400 InvalidArgument`.
- Smoke test, doc 321, one call per mode (read-only). The full schema is accepted, the output
  is strict JSON in schema key order, and nothing needed salvage. Tokens and latency are within noise of
  `json_object` (2486 in both; 2600 vs 2650 out; 11.4s vs 12.2s). One field pair differed
  (`subtotal`/`tax_amount`), which one sample at temperature 1.0 cannot attribute.
- Both modes read doc 321's date as `2019-09-26`, so the schema alone is not the #63 fix.

## Design

```
extract_document()
  │  prompt  = build_extraction_prompt(...)        field list in _FIELD_SCHEMAS order
  │  schema  = build_extraction_schema(exp, iss)   _FIELD_SCHEMAS + category enum
  │  kwargs += response_format_kwargs(model, "receipt_extraction", schema)   if llm_json_mode
  │              supports_response_schema(model)? ── yes ─> json_schema
  │                                               └─ no / raises ─> json_object
  ▼
  for attempt in retries+1:
      call ── is_schema_rejection(e) AND mode==json_schema AND not yet fallen back
      │         (a plain BadRequestError whose message names a schema)
      │         └─> ERROR log, switch kwargs to json_object FOR THE REST OF THIS DOC,
      │             re-call (not a parse retry)
      │      ── any other error ──> raise at once, no extra call (as today)
      ▼
      parse_llm_response()  (tolerant ladder, unchanged: still the net)
```

### 1. One ordered field list (decision 4A); transcribe-first tried and removed (3B, gate run 1)

`_FIELD_SCHEMAS`: a module-level ordered dict mapping field name → JSON Schema fragment. It is
the single source of truth for:

- **the schema**: `build_extraction_schema(expense_categories, issued_categories)` copies
  it, injects the category enum, and sets every property `required` and every object
  `additionalProperties: false`. That is what OpenAI strict mode demands, so the same schema
  survives a model swap.
- **the shape gate**: `_EXPECTED_KEYS = frozenset(_FIELD_SCHEMAS)`.
- **the prompt**: stays hand-written, because it carries guidance a schema cannot (e.g. the
  `total_amount` tip paragraph). A test pins its field lines to `_FIELD_SCHEMAS` keys
  **in order**, anchored on `^- (\w+):` so the 10-line `total_amount` entry parses.

**Transcribe first (3B) was built, measured, and taken out.** The plan moved
`raw_extracted_text` to the top of `_FIELD_SCHEMAS` and of the prompt's field list; gate run 1
(§4) blocked it, and the order is back to master's. The original rationale follows. Under constrained decoding, keys are emitted in declaration
order, and an earlier field is the model's scratchpad for the later ones. Today the model
commits to `receipt_date` before it transcribes the receipt. Every other field keeps its
relative order. The prompt reorder applies to the `json_object` fallback too. The gate
(§4) measures the reorder directly on misdated documents.

| Field(s) | Fragment |
|---|---|
| 13 text fields | `{"type": ["string", "null"]}` |
| `subtotal`, `tax_amount`, `total_amount` | `{"type": ["number", "null"]}` |
| `line_items` | array of `{description: string, quantity: number\|null, unit_price: number\|null}` |
| `additional_fields` | array of `{key: string, value: string}` |
| `document_type` | enum of the three types |
| `category` | enum of the user's category names, deduped, **plus null**. A plain nullable string when the list is empty (an empty `enum` is invalid). The enum merges both sections, so the model can still pick a category from the wrong section; `pipeline.py:114-121` still NULLs that, exactly as today. The schema does not close that gap. |
| `extraction_confidence` | `{"type": "number"}`, required and not nullable. Measured: a missing confidence has never occurred on a successful extraction (null appears only on 3 failed rows and one pre-model April row). The model signals "unreadable" with 0.0, which it has used 8 times, and 0.0 still routes to needs_review. |

No `minimum`/`maximum`/`format`: support varies by provider, and the parser already
range-checks confidence (`extract.py:359`).

### 2. Mode selection: no new setting

`response_format_kwargs(model, name, schema)` sits next to `reasoning_effort_kwargs` and
mirrors it. With `llm_json_mode=true` it sends json_schema where litellm says the model takes one, and
json_object otherwise; `drop_params=True` either way. With `false` it sends nothing (unchanged).
If `supports_response_schema` raises (unknown model id), that counts as unsupported.

**Schema rejected at runtime.** This is documented, not hypothetical: a large category enum can
400. On a schema rejection from a `json_schema` call (`is_schema_rejection`: a plain
`litellm.BadRequestError` whose message names a schema), extraction logs ERROR, switches
this document's remaining calls to `json_object`, and re-calls. The fallback is not a
parse retry and does not consume `llm_parse_retries`. This preserves the invariant the
`drop_params` comment states: a model swap must never brick extraction. Anything else is
raised at once with no extra call: the context-window, content-policy and image-fetch
subclasses (/review), and a plain 400 that does not name a schema, such as the Gemini 403s
(revoked key, billing) that litellm 1.93 maps to `BadRequestError` (/ship decision D1).

**Undo is a redeploy (tension 2, kept).** There is no runtime kill switch. Rollback means re-tagging
`rollback-pre-<sha>` and running `up -d`, which takes about a minute and is written in every deploy report.
A repeating schema 400 shows up as one ERROR line per document, and that line is the alert.

**Test LLM probes the schema (TODO 1, built here).** `POST /settings/test-llm`
(`settings.py:122`) adds a second, tiny call through `extraction_format_kwargs`, the helper
extraction itself uses. It sends **the extraction schema, with the current category enum**,
and a minimal prompt, because the documented failure is a large enum, and a toy
one-field schema would pass where the real one 400s.
It returns `"schema": "supported" | "unsupported" | "off" | "rejected" | "error"` ("off"
when `llm_json_mode` is off; "error" for any other failure of the probe itself) plus
`schema_detail`, with the API key masked out of provider error text. Both calls have a 60s
timeout. The category query moved into `pipeline.extraction_categories()`, shared by the
pipeline and the probe. `SettingsPage.tsx` shows the result on its own "Structured output:"
line, green for supported, grey for off, amber for anything that falls back. A model
swap's schema support then shows when you click Test, not in the container log.

### 3. Triage: one helper (decision 5A), ingest-all kept (decision 2A)

`_llm_select(content, key, allowed, label)` in `url_triage.py` owns the whole block the
three functions copy today: settings, the call, schema kwargs, parse, filtering to the
allowed list, and the fallback. Each public function keeps only its prompt.

- The reply is wrapped as `{"<key>": [...]}`, because OpenAI's `json_object` and strict mode both require a
  top-level object. A bare list is still accepted, so a model that ignores the schema
  still triages. The schema is an object with one required array-of-strings property.
- It honors `llm_json_mode` like extraction.
- **Any failure ingests everything (2A).** A junk document is visible and costs one
  delete; a dropped receipt is invisible. The subsumed TODO's "drop on failure" proposal
  is declined for that reason.
- Settings loading is unified. `triage_telegram_urls` currently reads settings outside its
  error guard (`url_triage.py:38-41`), while the other two catch `RuntimeError`. All three
  now fall back.
- **/review decision D1: the same schema fallback as extraction** (`complete_with_schema_fallback`).
  Without it, a model that rejects the selection schema would make triage keep everything on
  every message, silently. Also from /review: only the leading JSON value is parsed (Gemini's
  trailing text no longer causes a keep-all), and the selection is strings only, deduplicated.

### 4. The A/B gate (decisions 1A, 6A; outside-voice fixes #1, #3, #4)

This builds on `chore/model-ab-harness` (fbd4fb9), cherry-picked onto this branch. It already
fixes the API-key lookup (`resolve_llm_api_key()`; the legacy setting has been empty on this
install since #25), generalizes the script to arms, and adds `--temperature`. **Expect a
conflict**: fbd4fb9 predates #54, and master has since changed the `backend.storage` import
line (adding `get_filed_path`) and `resolve_pdf`. Keep master's side of both.

This PR adds:

- `--ids 1,2,3`: pin the sample, so every run sees identical documents.
- `--save FILE`: per arm, per document, **every** extraction field (not just the 6 in
  `COMPARE_FIELDS`), plus the outcome. A failure is recorded as `{"error": ..., "truncated": bool}`
  instead of the document being skipped. Truncation means `finish_reason == "length"`.
- Pass the DB's `llm_parse_retries` and `llm_reasoning_effort` into `extract_document`,
  as the pipeline does. Today the harness runs with defaults 0 and `"none"`.
- `--compare BASE.json CAND.json`: offline, no LLM calls. It works on the **intersection** of document
  ids and reports per field:
  - `w_base`, `w_cand`: documents where a tree's two runs disagree (the noise floor);
  - `x`: documents where base run 1 and cand run 1 disagree.

  It also reports failures and truncations per tree, plus year-correct counts on the misdated set.

The harness only uses `extract_document` parameters that exist on master (`json_mode`,
`parse_retries`, `reasoning_effort`, `temperature`), so the branch's copy runs unchanged
against master's code.

**Procedure.** It is read-only: it writes no rows and changes no stored document. About 104
extractions, roughly $0.70.

1. Pin ids once: 18 stratified documents plus 8 of the 28 misdated ones, spread across both
   mechanisms. Record them in this plan.

   **Pinned 2026-09-23:**
   `308,286,241,324,330,298,256,85,36,239,113,116,302,120,224,260,273,263,60,111,315,321,32,119,229,252`.
   - 16 stratified: this includes misdated #36 and #120, which the gate detects on its own.
   - 2 more issued invoices, to other clients (#273, #263).
   - 8 misdated: #60 #111 #315 #321 are DD/MM/YY swaps; #32 #119 #229 #252 have the wrong year on the right day.
   - Mix: 21 expense receipts, 4 issued invoices (#286 #330 #273 #263), 1 other document.

   Implementation found that `pick_sample` could not produce this. It sorted buckets
   alphabetically by (type, vendor), so expense vendors filled every slot and the first
   draw was 26/26 expense receipts. `stratify()` now interleaves types, with a test.
2. `git worktree add --detach <scratch>/master-wt master`. Copy the branch's harness into
   it (untracked). Run it with **the main checkout's `.venv/bin/python`** (the worktree has
   no venv) and an **absolute `--data-dir /mnt/user/appdata/Receiptory/data`**. No `.env`
   is needed, since the key resolves from the DB. Arms: `json_object × 2`, `--save <scratch>/base.compare.json`. This is
   today's production behaviour plus its noise floor.
3. On the branch, same interpreter and data dir: arms `json_schema × 2`, `--save <scratch>/cand.compare.json`.
4. `--compare base.json cand.json`.

**Gate run 1 (2026-09-23): BLOCK. Schema and transcribe-first together, against master.**

| | master | branch (schema + reorder) |
|---|---|---|
| Failed runs | 1 (#60, `Expecting ',' delimiter`) | 0 |
| Year correct, correctly stored docs | 30/30 | 27/30: #116 ×2, #260 read "2026" as **2020** |
| Year correct, misdated docs | 7/22 | 6/22 (no gain) |
| `subtotal` / `tax_amount` null | 5/51, 7/51 | 10/52, 12/52; systematic on #321 and #239 |
| Line items | 96, 4 with nulls | 96, none (noise in wording, not a regression) |

The `vendor_name` and `vendor_receipt_id` blocks were checked per document: they are variant
spellings and scattered one-off misreads on both sides. **Decision (owner): drop the
reorder, re-gate schema-only**, because transcribe-first measured no gain on the dates it was
for. `raw_extracted_text` went back to its master position, and the prompt text is
byte-identical to master's. Cost: $1.32 (master) + $1.55 (branch); the plan's ~$0.70 estimate
was about 4× low (roughly 2.5¢ per extraction at registry rates).

**Gate run 2 (2026-09-23): PASS. Schema only, prompt byte-identical to master's.**

| | master | schema only |
|---|---|---|
| Failed runs | 1 | **0** |
| Year correct, correctly stored docs | 30/30 | 29/30 (#116 once: the same "2026"→"2020" slip master makes on #252) |
| Year correct, misdated docs | 7/22 | 7/22 |
| `tax_amount` / `subtotal` null | 7/51, 5/51 | 9/52, 8/52 |
| Field excess over noise floor | — | max +1 (none blocks) |

The residual is slightly more missing `tax_amount`/`subtotal`: +2 and +3 runs of ~52. That's
indistinguishable from noise at n=26 (master's own tax noise is 3 documents; #321 reads 4.36
on one of two runs). It's far smaller than run 1's +5/+5, so the reorder was the main cause.
It is watched in rollout, not claimed to be zero. Gate total: $4.30 across three runs
(saved result files: `base.json`, `cand.json`, `cand2.json` in the session scratchpad).

**/review decision D2: dates gate only against verified truth.** The year check above assumed a
receipt's true year is its upload year. That held for this sample (every misdated document was
hand-confirmed in the #63 analysis), but it scores backlog uploads backwards. `--verified-dates
FILE` ({"id": "YYYY-MM-DD"}) now scores year and exact date against checked dates and can block;
without it, the upload-year check is reported and never blocks, and undated documents no longer
count as misdated. `--compare` also refuses file pairs that are not two `--control` runs of one
configuration, and `--save` writes only `*.compare.json` names (gitignored; a gitignored path is
not a safe one, since `data/receiptory.db` and `.env` are ignored too).

**/ship additions.** The gate also blocks when fewer than half the documents were comparable
(every run failing on both sides used to print PASS), and on any candidate run whose schema was
rejected (`output_mode == json_object_fallback`). The report lists each tree's output modes and
flags fields where the candidate is noisier than the baseline by 2+ as `(less stable)`,
informational only (/ship decision D2).

**Gate. Any of these blocks the merge pending per-document inspection:**
- for any field, `x − max(w_base, w_cand) ≥ 2` (drift beyond the noise floor);
- more failures or more truncations on the branch than on master;
- fewer year-correct dates on the misdated set on the branch than on master.

Watch `tax_amount`/`subtotal` (the smoke-test difference) and `receipt_date` in particular.

### 5. Docs

- `CLAUDE.md` "LLM JSON handling": schema-first, the json_object fallback, `_FIELD_SCHEMAS`
  as the field list, and why transcribe-first was measured and removed.
- `TODOS.md`: move the two subsumed TODOs to Completed. (The new Gemini `temperature`
  TODO was added during the review.)
- Comment maintenance: rewrite `extract.py:440-447`. The `parse_llm_response` ladder
  diagram and the `_as_float` docstring stay accurate.
- `jsonschema` (4.26, already locked as a litellm dependency) becomes an explicit dev
  dependency.

## Tests

**extract.py**
- `_FIELD_SCHEMAS`: the prompt's `^- (\w+):` lines equal its keys, in order (master's field order).
- `build_extraction_schema`:
  - the category enum is the deduped names plus null, including names duplicated across sections;
  - empty categories give no enum;
  - every object level has `required == list(properties)` and `additionalProperties: false`;
  - `SAMPLE_LLM_RESPONSE` validates against it with `jsonschema`.
- `response_format_kwargs`: supported → json_schema; unsupported → json_object; registry
  raises → json_object; `drop_params` is always True. Tests patch
  `litellm.supports_response_schema` rather than depend on the registry.
- `extract_document`:
  - json_mode with a supported model → the schema is sent (rewrite `test_extract_document_requests_json_mode`);
  - **REGRESSION (critical):** json_mode with an unsupported model → `json_object` is still sent,
    as every model gets today;
  - json_mode False → nothing is sent (existing test);
  - 400 on json_schema → exactly one json_object re-call. A later parse retry in the same
    document stays on json_object, the fallback does not consume a parse retry, and tokens of
    successful calls are counted;
  - 400 on json_object → raises, no loop.
- Parse-ladder tests unchanged: the ladder is still the net.

**url_triage.py**, for each of the three public functions:
- a wrapped object is parsed and filtered;
- a bare list is tolerated;
- not-a-list or a missing key → ingest all;
- the call raises → ingest all;
- settings `RuntimeError` → ingest all (a behaviour change for Telegram);
- schema kwargs are sent when json_mode is on, and none when it's off;
- temperature is still passed (existing test).

Email-URL triage and document classification also gain happy-path and fallback tests; today
they have only the temperature test.

**settings.py `test-llm`** (no tests today): connectivity ok with schema supported; schema
unsupported (registry says no) → `"unsupported"` with no second call; schema call 400 →
`"rejected"` plus the message, while connectivity is still reported ok; JSON mode off → `"off"`
with no second call; a non-schema 400 → `"error"`, not `"rejected"`; the key is masked in error
text; connectivity fails → 500, as today.

**scripts/compare_json_mode.py**: arm construction and argument validation (`--ids`
parsing, `--compare` needs two files, `--model-a`/`--model-b` pairing kept from fbd4fb9).
The compare arithmetic is tested on hand-written result files: intersection, noise floor, the
`≥ 2` rule, failures and truncations counted, year-correct counting. No LLM calls.

**[→EVAL]** the §4 gate on real documents, before merge.

## Rollout

Deploy as usual, with a `rollback-pre-<sha>` tag. Watch for a week:
- `parse failed` count (expect ~0);
- **truncation errors** (`truncated at max_tokens`; expect no rise);
- schema-fallback ERROR lines (expect 0 on Gemini);
- the `needs_review` share on new documents (expect unchanged);
- **the share of new receipts with `tax_amount` null** (gate run 2 residual: 9/52 vs 7/51).
  Compare it with the 30 documents before deploy. If it's clearly up, the schema is dropping
  VAT, and it's worth a prompt nudge or an A/B on a larger sample;
- the first new receipts' dates and totals, by eye.

## NOT in scope

- **#63 date handling**: `receipt_date_as_printed`, the DD/MM/YY resolver, and the repair of the
  28 misdated documents. This PR only builds the foundation and measures the reorder.
- **Re-extracting existing documents**: standing constraint. The gate re-reads some
  read-only and stores nothing.
- **Gemini `temperature` deprecation**: TODO added. Kept out so the gate doesn't measure a
  third change.
- **Runtime kill switch / three-way output setting**: tension 2. Redeploying is the undo.
- **Remembering a schema rejection across documents**: tension 2. The per-document ERROR line is
  the alert, and the extra call is unbilled.
- **Section-aware category enum** (`if document_type == issued_invoice then enum issued`): the
  conditional schema is complex for the gain, and `pipeline.py:114-121` already handles it.
- **Changing the truncation retry policy**: measure first (gate + rollout watch). Change it
  only on evidence.
- **Triage "drop on failure"**: declined (2A).

## What already exists

| Existing | Reused? |
|---|---|
| Tolerant parse ladder `parse_llm_response` (`extract.py:256`) | Reused unchanged as the net for fallback modes |
| `reasoning_effort_kwargs` helper pattern (`extract.py:409`) | Mirrored by `response_format_kwargs` |
| `drop_params` "never brick extraction" invariant (`extract.py:441-447`) | Kept, extended to triage |
| `url_triage` importing helpers from `extract.py` (`url_triage.py:10`) | Reused for `response_format_kwargs` |
| `chore/model-ab-harness` fbd4fb9 (arms, key fix, `--temperature`) | Cherry-picked, extended (1A) |
| `compare_json_mode.py` read-only guards (missing DB, schema version) | Kept |
| `pipeline.py:80-85` needs_review routing on confidence | Unchanged; 0.0 still routes |
| Worktree = fresh-clone shape (2026-09-16 learning) | Used for the master baseline |

## Failure modes

| New codepath | Realistic failure | Test | Handling | User sees |
|---|---|---|---|---|
| json_schema call | Category enum grows until Gemini 400s | yes | json_object fallback | ERROR line per document; extraction succeeds |
| json_schema decoding | Repeating string until max_tokens | existing truncation tests | fails document, not retried | `failed` + "truncated at max_tokens" in UI; gate and rollout watch it |
| Model ignores the schema | `drop_params` dropped it on a new model | yes (unsupported path) | parse ladder | nothing; salvage WARNING in log |
| Transcribe-first order | Accuracy drifted (tax nulls, 2026 read as 2020) | gate run 1 | **gate blocked it; removed** | n/a |
| Nullable line-item description (found by /review) | One null description 500s `GET /documents` | yes | schema requires a string; parser coerces | nothing |
| `_llm_select` | Reply is an object without the key | yes | ingest all (2A) | junk document to delete |
| `_llm_select` settings | DB unavailable on the Telegram path | yes (new) | ingest all | nothing |
| test-llm probe | Schema call 400s | yes | reported as `rejected` | clear line in Settings |
| Empty category list | Empty `enum` is invalid | yes | no enum emitted | nothing |

**No critical gaps**: every path has a test, handling, or a visible signal.

## Worktree parallelization strategy

| Step | Modules touched | Depends on |
|---|---|---|
| S1 extraction schema, mode, fallback | `backend/processing/` | — |
| S2 triage helper | `backend/ingestion/` | S1 (`response_format_kwargs`) |
| S3 test-llm probe | `backend/api/`, `frontend/src/pages/` | S1 |
| S4 harness (cherry-pick + ids/save/compare) | `scripts/` | — |
| S5 gate run | none (read-only runs) | S1, S2, S4 |
| S6 docs | `CLAUDE.md`, `TODOS.md`, `pyproject.toml` | S1-S3 |

- Lane A: S1 → S2 → S3 (shared helper, sequential).
- Lane B: S4 (independent of backend code).
- Then S5, then S6.

Launch A and B in parallel. They share no module directory, so there are no conflict flags. At CC speed
the lanes finish in minutes, so running them sequentially in one session is equally fine.

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above.

- [x] **T1 (P1, human: ~3h / CC: ~20min)** — extraction — `_FIELD_SCHEMAS`, `build_extraction_schema`, derived `_EXPECTED_KEYS` (the planned reorder was built, gated, and removed)
  - Surfaced by: Code quality 4A; Architecture 3B
  - Files: `backend/processing/extract.py`, `tests/test_extract.py`
  - Verify: `uv run pytest tests/test_extract.py`
- [x] **T2 (P1, human: ~3h / CC: ~20min)** — extraction — `response_format_kwargs` + wiring + 400 fallback + the unsupported-model regression test
  - Surfaced by: plan §2; Test review regression
  - Files: `backend/processing/extract.py`, `tests/test_extract.py`
  - Verify: `uv run pytest tests/test_extract.py tests/test_pipeline.py`
- [x] **T3 (P1, human: ~2h / CC: ~15min)** — ingestion — `_llm_select` helper, wrapped schema, unified settings guard, tests for all 3 functions
  - Surfaced by: Code quality 5A; Test review (2 functions untested)
  - Files: `backend/ingestion/url_triage.py`, `tests/test_url_triage.py`
  - Verify: `uv run pytest tests/test_url_triage.py`
- [x] **T4 (P1, human: ~1h / CC: ~10min)** — settings — test-llm schema probe + frontend clause + tests
  - Surfaced by: TODO 1 (built in PR)
  - Files: `backend/api/settings.py`, `frontend/src/pages/SettingsPage.tsx`, `tests/test_settings_api.py`
  - Verify: `uv run pytest tests/test_settings_api.py`; `cd frontend && npm run build`
- [x] **T5 (P1, human: ~4h / CC: ~30min)** — harness — cherry-pick fbd4fb9; `--ids`, `--save` (all fields, failures, truncations), `--compare` (intersection, noise floor, `≥2` rule, year-correct); pass parse_retries/reasoning_effort; tests
  - Surfaced by: 1A, 6A, outside voice #1 #3 #4
  - Files: `scripts/compare_json_mode.py`, `tests/test_compare_json_mode.py`
  - Verify: `uv run pytest tests/test_compare_json_mode.py`
- [x] **T6 (P1, human: ~1h / CC: ~15min + run time)** — eval — run the gate (master worktree ×2, branch ×2, 26 pinned ids), record the result here
  - Surfaced by: 6A; tension 1A (misdated docs)
  - Files: this plan
  - Verify: `--compare` output meets the gate
- [x] **T7 (P2, human: ~1h / CC: ~10min)** — docs — CLAUDE.md, TODOS.md completions, extract.py comments, `jsonschema` dev dependency
  - Surfaced by: plan §5; Code quality comment maintenance
  - Files: `CLAUDE.md`, `TODOS.md`, `pyproject.toml`, `uv.lock`
  - Verify: `uv sync --all-extras`; full `uv run pytest tests/`

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found (Claude subagent; Codex not installed) | 8 findings: 3 tensions decided, 5 fixes accepted |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 28 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CROSS-MODEL:** the outside voice agreed with the review's original 3A recommendation on field order; the owner chose 3B, gate run 1 blocked it, and it was removed. It disagreed on the kill switch (kept: redeploy is the undo) and on scope (kept: #63 is the goal, and the Problem section now says so).
- **VERDICT:** ENG CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
