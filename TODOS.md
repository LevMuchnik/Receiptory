# TODOS

Deferred items captured during planning and review. Organized by component, sorted by priority (P0 highest). Each entry includes the why, current state, and where to start.

## Testing

### Fix pre-existing auth login test failure (401)

**What:** `tests/test_auth_api.py::test_login_success` returns 401 instead of 200. Root-cause and fix.

**Why:** The failure predates the issue #10 branch (fails on master too) and will fail every future CI/ship run until fixed. May be environment-specific — observed in a containerized run of the suite (`docker run` from the production image with a fresh `uv sync` venv); worth reproducing in the normal dev environment first.

**Context:**
- Noticed by /ship on 2026-07-20 while shipping `fix/10-tolerant-json-parse`
- Error: `assert 401 == 200` at tests/test_auth_api.py:33 (the `client` fixture logs in with admin/testpass after setup)
- The companion stale-assert failure (`test_migration_version`) was fixed in that same PR; this one needs investigation, not a one-liner
- Start: run the single test in the standard dev env; if it passes there, the bug is in the container test env (bcrypt wheels?), not the app

**Effort:** S
**Priority:** P0
**Depends on:** None

## Extraction

### Typed response_schema structured output for extraction

**What:** Upgrade the extraction LLM call from `json_object` mode to a full typed schema (litellm `response_format` with `json_schema`, mapping to Gemini's `response_schema`) so the model is constrained to the exact field names and types `parse_llm_response()` expects.

**Why:** JSON mode (shipped with issue #10) guarantees *syntactically* valid JSON but not the right shape — wrong field names, string-where-number, or missing keys still pass silently into `data.get()` defaults.

**Pros:**
- Eliminates a whole class of silent field-level extraction misses
- The schema doubles as executable documentation of the extraction contract

**Cons:**
- Gemini's schema dialect has quirks (limited unions/nullability support)
- Over-constraining can degrade extraction quality on messy receipts — needs its own eval
- Requires a drift check before becoming default

**Context:**
- Source: eng review of issue #10 (2026-07-20), decision D15
- `scripts/compare_json_mode.py` (built for the #10 merge gate) is exactly the A/B harness this experiment should reuse — extend it to a third arm (schema mode)
- The extraction fields live in `ExtractionResult` (backend/processing/extract.py) and the prompt's Required Output section

**Effort:** M
**Priority:** P2
**Depends on:** Issue #10 shipped (json_object mode live and stable)

## Ingestion

### Harden url_triage JSON parsing (mirror extraction #10)

**What:** Give the three `url_triage.py` LLM calls (`triage_telegram_urls`, `triage_email_urls`, `classify_email_documents`) the same JSON robustness extraction got in #10: `response_format={"type":"json_object"}` + `drop_params=True`, and reuse the tolerant parse ladder instead of a single strict `json.loads(_strip_code_fences(...))`.

**Why:** Today a malformed/degenerate LLM response makes triage fall through to its fallback — `return list(urls)` / `return fallback` — which silently ingests ALL urls/documents, including junk. Extraction is protected against this; triage is not.

**Pros:**
- Closes the last unprotected LLM-parse path in the pipeline
- Makes triage failure explicit (drop) rather than silent (ingest everything)
- json_object mode also suppresses the class of litellm unsupported-param warnings that extraction avoids via `drop_params`

**Cons:**
- Touches a second subsystem's robustness — deliberately kept out of the temperature-alignment PR (issue #11) to stay right-sized
- Needs its own tests for all three call sites (malformed response → correct fallback)
- The tolerant ladder lives in `extract.py` (`parse_llm_response`) — sharing it means a small refactor to expose it

**Context:**
- Source: eng review of issue #11 (2026-07-20), tension 2 / deferred from the temperature work
- Call sites + strict parse: `backend/ingestion/url_triage.py:67-78, 133-148, 215-231`
- Reference implementation: extraction's json_mode + tolerant ladder in `backend/processing/extract.py` (see the `drop_params` comment at :304-311)

**Effort:** M
**Priority:** P2
**Depends on:** None (independent of #11; can land any time)

## Scanner

### Expand test corpus + per-bucket tagging

**What:** Add a bucket field to the Scanner Lab (cluttered desk / dark background / harsh shadow / crumpled-long thermal), tag frames, and grow the corpus toward ~40+ per bucket for a statistically meaningful per-bucket eval.

**Why:** The current 31-frame aggregate eval is directional only (~±17pp binomial CI at IoU≥0.85) and cannot see the crumpled/long-thermal failure mode that motivates the ML swap — yet that bucket is exactly what triggers the fine-tune contingency. Per-bucket numbers on a larger corpus turn the go/no-go from a coin-flip into a real measurement.

**Pros:**
- The `scanner_test_frames.notes` field + PATCH API already support tags; only a Lab UI field is missing
- Makes the eval-gated default decision defensible per-bucket, not just aggregate

**Cons:**
- Meaningful per-bucket power needs many more labeled frames (phone-in-hand capture + annotation time)
- Lab UI change (bucket dropdown wired to the notes field)

**Context:**
- Source: eng review 2026-07-06 — outside voice (n=31 statistical power) + D1 (aggregate-only eval chosen for v1)
- Storage: `scanner_test_frames.ground_truth_json` (normalized quads) + `notes` (bucket tag)

**Effort:** M
**Priority:** P3
**Depends on:** Only matters if the v1 aggregate eval is borderline and a sharper per-bucket decision is needed

### WebGPU-guard for the live ML detection loop

**What:** In the live viewfinder, run the ML detector only when WebGPU is available. If WebGPU is absent, keep the classical detector in the live loop and use WASM-backed ML only in the Lab / eval — never per-frame.

**Why:** Single-threaded WASM ML at 256px (the chosen fallback, no COOP/COEP) can take 100–300ms+ per inference and jank the viewfinder. WebGPU (present on the S26 Ultra) makes this a non-issue, but a weaker device would degrade badly. A capability check keeps the live loop smooth everywhere.

**Pros:**
- Sidesteps janky-WASM-per-frame entirely via one `navigator.gpu` check
- Composes with the A2 classical-fallback-with-indicator path already being built

**Cons:**
- Adds a capability-branch to the live detector selection
- Means ML is "not live" on non-WebGPU devices even when selected (acceptable for a single-user WebGPU device)

**Context:**
- Source: eng review 2026-07-06, D8 (live detection cadence/threading), option C deferred by user
- Design: `~/.gstack/projects/LevMuchnik-Receiptory/root-master-design-20260703-134251.md`
- Related: A1 decision (self-host WASM single-threaded, no COOP/COEP)

**Effort:** S
**Priority:** P3
**Depends on:** ML detection is shipped and live (Phase 1 of the ML detector plan). Only worth building if a non-WebGPU device enters the picture, or the S26 WASM fallback benchmark is bad

### Web Worker for live detection

**What:** Move the detection pipeline (preprocess + inference + parse) to a Web Worker using OffscreenCanvas + transferable ImageData, so the main thread never blocks during live detection.

**Why:** Detection currently runs on the main thread, competing with the camera preview and React render. If the S26 Ultra benchmark shows the every-Nth-frame main-thread loop stuttering, a worker is the fix.

**Pros:**
- Main thread stays smooth regardless of detector cost
- Use transferable objects to avoid the 10–30ms ImageData clone cost

**Cons:**
- ORT-web + WebGPU-in-worker support varies; OffscreenCanvas adds complexity
- The design deliberately deferred this ("no Web Worker" simplification)

**Context:**
- Source: eng review 2026-07-06, D8, worker explicitly deferred
- Only build if the S26 Ultra viewfinder actually janks under the chosen every-Nth-frame + smoother approach

**Effort:** M
**Priority:** P3
**Depends on:** Phase 1 shipped; on-device benchmark shows main-thread stutter

### Test set as ML fine-tuning corpus

**What:** Once the scanner test set has 500+ annotated frames, evaluate fine-tuning the Sprint 3 ML detector on the user's distribution (Hebrew thermal-paper receipts, his specific lighting, his specific surfaces).

**Why:** Off-the-shelf document-corner-regression models are trained on US/EU office documents and standard receipts. They likely leave accuracy on the table for Hebrew thermal-paper receipts on home-NAS-user surfaces. Fine-tuning on the user's own annotated frames closes that gap.

**Pros:**
- Test-set storage shape (`scanner_test_frames` table + JPEGs + `ground_truth_json`) is already exactly right for export to a training dataset — no schema work needed
- Even modest fine-tuning (last few layers, 100 epochs on 500 frames) tends to lift hit rate noticeably for narrow distributions
- Compounds the value of every annotated frame the user has accumulated

**Cons:**
- Fine-tuning needs a careful eval-on-held-out-split process to avoid overfitting and regressing on out-of-distribution receipts
- Requires a Python training pipeline (PyTorch / ONNX export) — not currently in the stack
- Probably ~1 week of work end-to-end, depending on model choice

**Context:**
- Source: eng review of `docs/plans/2026-05-10-scanner-detection-overhaul.md`, decision D9
- Storage shape: see Sprint 1.5 of that plan (`scanner_test_frames` table)
- Ground truth shape: same as `Quad` type (4 corners, normalized 0–1)
- The Sprint 3 model selection (3.1 in the plan) should prefer architectures with documented fine-tuning recipes — don't pick a model whose weights are fixed-only

**Effort:** XL
**Priority:** P3
**Depends on:** Sprint 3 ships first and proves off-the-shelf accuracy is meaningful but insufficient. Test set has accumulated ≥500 annotated frames (likely 2–3 months of daily use post-Sprint 1). User has access to a machine that can run Python training (NAS likely cannot; cloud GPU rental or local workstation)

### Persisted longitudinal eval store

**What:** The `scanner_eval_results` + `scanner_eval_runs` tables + POST/GET API originally in the design (dropped in the 2026-07-06 review). Persist per-run detector results so runs survive reload and can be diffed over time.

**Why:** If the eval gets re-run across many models (e.g. a series of fine-tuned DocAligner variants), a persisted store lets Claude diff runs and track accuracy/timing trends instead of one-shot JSON dumps.

**Pros:**
- Longitudinal comparison across model versions
- Reuses the already-labeled `scanner_test_frames` corpus

**Cons:**
- Two new SQLite tables + API surface for what is today a handful of one-off runs
- Duplicates what the Python spike computes for accuracy

**Context:**
- Source: eng review 2026-07-06, D10 — narrowed the eval to Python-accuracy + a thin on-device timing JSON probe; persistence deferred
- Only justified once there's a multi-run eval *program* (fine-tune flywheel active)

**Effort:** M
**Priority:** P4
**Depends on:** Fine-tune flywheel active, or a recurring need to compare many model versions

## Completed

_No completed items yet._
