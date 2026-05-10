# Scanner Detection Overhaul — Sprint Plan

**Goal:** Cut receipt-boundary detection failures (text-block lock-on, degenerate quads, white-on-white blindness) by replacing single-frame scanic-only detection with a configurable detector pipeline that supports classical + ML approaches, evaluated against a real per-user test set.

**Approach:** Three sprints, plus a foundation slice landed in Sprint 1. Every accepted scan auto-deposits its raw camera frame + detection metadata into a server-side test set. A new admin "Scanner Lab" tab lets the user tune parameters and A/B detectors against that real test set, then activate a winning profile back into the live scanner.

**Non-goals:**
- Cross-user / multi-tenant tuning (single-user app)
- Server-side detection (everything runs in the browser; backend only stores frames + metadata + active profile)
- Replacing scanic outright in Sprint 1 — wrapping it with preprocessing, scoring, and temporal smoothing

---

## Decisions from eng review (2026-05-10)

| # | Topic | Decision |
|---|---|---|
| D1 | Scope commit | Proceed with all 3 sprints as planned. |
| D2 | Ground-truth bootstrapping | Lab-only annotation. Accept that early test-set is biased toward Sprint 1's detector; correct later via lab annotation sessions. |
| D3 | Candidate set source | Multi-pass scanic: run on raw frame, shadow-normalized frame, and saturation-masked frame. Union returned quads, dedupe by IoU, score them all. |
| D4 | Eval timing portability | Tag every eval run with `userAgent`, EP (`webgpu` / `wasm-simd` / `wasm`), CPU cores. Lab UI groups by device-class. Sprint 3.5 phone gate explicitly requires a phone-tagged run. |
| D5 | Active-profile sync | Document "reload scanner to apply." No code-level cross-tab notification. Banner in lab after activation. |
| D6 | Detector lifecycle | `useDetector(name, config)` hook. Init on mount + on deps change, dispose on unmount + before re-init. Single ownership; consumed by both ScannerPage and Lab Live tab. |
| D7 | Sprint 2 test scope | Explicit test enumeration in plan: retention edge cases, eval-runner contract tests, one E2E (annotate → save → reload → persisted). |
| D8 | Per-frame perf | Web Worker for the entire detection pipeline (preprocess + scanic + scoring). Stack blur instead of Gaussian. |
| D9 | Test set as ML training corpus | Captured as a TODO; not Sprint 3 scope. |

**Auto-applied (no separate decision):**
- Regression test for `RECEIPTORY_SCANNER_DETECTOR=legacy_scanic` rollback path (iron-rule regression).
- TemporalSmoother tests cover: empty buffer first call, alternating null/valid sequences, outlier rejection.
- `detector_name` field uses `name@version` format (e.g., `classical@2`).
- `ground_truth_json` reuses `Quad` shape (no new schema).
- `POST /test-frames` on hash conflict returns existing record with 200 (idempotent).
- Storage cap eviction: LRU on `captured_at`; annotated frames sticky.
- `ParamSchema` is a discriminated union: `range | enum | toggle | number`.
- Raw frame upload from `useScanner` is fire-and-forget (does not block scan accept flow).

---

## Architecture Decisions

### D1: Detector interface (single contract, three implementations over time)

```ts
// frontend/src/lib/scanner/detector.ts
export type Quad = { topLeft: Pt; topRight: Pt; bottomRight: Pt; bottomLeft: Pt };
export type ScoredQuad = { quad: Quad; score: number; breakdown: Record<string, number> };

export interface DetectionResult {
  corners: Quad | null;        // chosen quad (or null)
  confidence: number;          // 0..1
  candidates: ScoredQuad[];    // for inspector; may be empty
  timingMs: number;
  detectorName: string;
}

export interface Detector {
  readonly name: string;
  readonly schema: ParamSchema;        // for admin UI to render param controls
  init(config: Record<string, unknown>): Promise<void>;
  detect(image: ImageData): Promise<DetectionResult>;
  dispose(): void;
}
```

Implementations:
- `ClassicalDetector` (Sprint 1): wraps scanic + adds preprocessing, scoring, hard rejects
- `MLDetector` (Sprint 3): onnxruntime-web, WebGPU primary, WASM fallback
- `HybridDetector` (Sprint 3): runs classical fast path, falls back to ML on low confidence / N stale frames

`TemporalSmoother` (separate, orthogonal) wraps any `Detector`, emitting smoothed corners + a stability score for auto-capture trigger.

### D2: Test-set storage — standalone table

```sql
CREATE TABLE scanner_test_frames (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  frame_hash TEXT NOT NULL UNIQUE,           -- sha256 of raw frame bytes
  document_id INTEGER,                       -- nullable; FK to documents (set if accepted scan)
  captured_at TEXT NOT NULL,                 -- ISO8601 UTC
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  detector_name TEXT,                        -- detector that produced corners_at_capture
  corners_at_capture_json TEXT,              -- detector output; nullable if user manually cropped
  ground_truth_json TEXT,                    -- nullable until annotated
  user_action TEXT NOT NULL,                 -- 'accepted' | 'retaken' | 'manual_crop'
  excluded INTEGER NOT NULL DEFAULT 0,       -- soft-exclude from eval
  notes TEXT
);
CREATE INDEX idx_test_frames_captured_at ON scanner_test_frames(captured_at DESC);
```

Raw frames live at `data/scanner_test_set/{frame_hash}.jpg` (JPEG q=85, ~200–500 KB each). Test-set lifecycle is decoupled from receipt deletion: deleting a `documents` row sets `document_id = NULL`, frame stays.

**Retention policy:** cap at `RECEIPTORY_SCANNER_TEST_SET_CAP` (default 1000). When over cap, prune oldest frames where `ground_truth_json IS NULL AND excluded = 0`. Annotated frames are sticky.

**Privacy:** frames contain receipt PII. Same trust model as the existing `originals/` directory — single-user self-hosted system on the user's NAS. No new exposure surface.

### D3: Evaluation runs in the browser

Browser-side evaluation reuses live-detector code (no port-to-Node bug class), and gives honest numbers on the device the user actually scans with. Cost: admin tab must be open and the phone must do the work — acceptable.

**Metrics reported per config-vs-set run:**
- **Hit rate**: % of frames where IoU(predicted, ground_truth) ≥ 0.85
- **Mean / median IoU** across all frames
- **False-quad rate**: predicted corners present but IoU < 0.5 (worse than null — actively wrong)
- **Null rate**: predicted corners null (no detection)
- **Median inference time** per frame (per detector)

Only frames with `ground_truth_json` set are counted. Unannotated frames are skipped from eval but still browsable in the inspector.

### D4: Profile activation writes back to settings

```sql
-- In existing settings table (key/value):
-- key = 'scanner_active_profile' → JSON: { detector: 'classical' | 'ml' | 'hybrid', config: {...} }
```

Admin "Activate" button on a saved profile writes this row. Live scanner reads it on mount. No "tuned but never deployed" gap.

---

## Sprint 1 — Classical-improved + Test-set foundation

**Duration estimate:** 4–6 days
**Ships:** improved live scanner replaces current behavior; test set begins accumulating immediately.

### 1.0 Detection worker scaffolding (NEW per D8)
- Create `frontend/src/workers/detection.worker.ts` skeleton
- `postMessage` protocol: `{ type: 'init', detectorName, config }`, `{ type: 'detect', imageData, requestId }`, replies `{ type: 'result', requestId, result: DetectionResult }`
- Worker imports detector implementations directly; main thread holds a thin `WorkerDetector` proxy that conforms to `Detector` interface
- Single-flight enforced on the worker side; main thread can fire-and-forget detect requests

### 1.1 Detector interface + scaffolding
- Create `frontend/src/lib/scanner/detector.ts` (types in D1)
- Create `frontend/src/lib/scanner/types.ts` (`Pt`, `Quad`, `ParamSchema` as discriminated union: `range | enum | toggle | number`)
- Create `frontend/src/lib/scanner/use-detector.ts`: `useDetector(name, config)` hook owning init/dispose lifecycle (per D6). On `(name, configHash)` change: dispose old, init new. On unmount: dispose. Returns `{ detector, ready, error }`.
- Refactor `useScanner` and `CameraViewfinder` to consume `useDetector` instead of calling `detectCorners` directly. Main thread receives a `WorkerDetector` proxy from D8.

### 1.2 ClassicalDetector — preprocessing (per D8: stack blur, runs in worker)
- New `frontend/src/lib/scanner/preprocess.ts`:
  - `shadowNormalize(imageData)`: grayscale → divide by **stack-blurred** copy (kernel ≈ width/8). Stack blur, not Gaussian — 5–10× faster, visually equivalent for this purpose. Target measured <10 ms at 0.4× scale on mid-range Android (verified at end of 1.2 before moving on).
  - `saturationMask(imageData)`: HSV S-channel, return float32 grid of `1 - S` (paper-likelihood prior)
  - `composeDetectionInput(luma, satMask, α, β)`: weighted sum, output ImageData fed into scanic
- Configurable params: `α`, `β`, blur kernel size, enable/disable shadow norm, enable/disable saturation prior
- All preprocessing runs inside the detection worker (D8) — never on the main thread

### 1.3 ClassicalDetector — multi-pass scanic + scoring (per D3)
- New `frontend/src/lib/scanner/scoring.ts`:
  - Hard rejects: area fraction outside [0.12, 0.95]; aspect ratio outside [0.4, 12]; min interior angle <50° or >130°; adjacent edge length ratio >8
  - Score: `w_area·area + w_convex·convexity + w_aspect·aspect_plausibility + w_edge·perimeter_edge_strength + w_uniform·interior_uniformity − w_text·text_density`
- **Multi-pass scanic candidate generation** (per D3):
  1. Run `scanic.scan` on raw frame → quad A
  2. Run `scanic.scan` on shadow-normalized frame → quad B
  3. Run `scanic.scan` on saturation-masked frame → quad C
  4. Union {A, B, C}, dedupe near-duplicates by IoU > 0.95
  5. Apply hard rejects
  6. Score remaining candidates, pick max. If empty after rejects, return null.
- Per-detection cost budget (worker thread, ~95 ms p50 on mid-range Android — acceptable inside worker since main thread is unblocked)

### 1.4 TemporalSmoother
- New `frontend/src/lib/scanner/smoother.ts`:
  - Ring buffer of last N detections (default 10)
  - Per-corner EMA, α default 0.4
  - Outlier rejection: drop frames whose corners are >X% (default 15%) of frame diagonal from EMA
  - `stability` ∈ [0, 1] = `1 − maxCornerDriftOverWindow / frameDiagonal`
  - `shouldAutoCapture()`: stability > threshold (default 0.9) for ≥K frames (default 8)
- `CameraViewfinder` consumes the smoothed quad for overlay; raw quad still available for the inspector

### 1.5 Test-set capture (foundation slice)
- Backend:
  - Migration `migrations/0XX_scanner_test_frames.sql` (D2 schema). Migration is reversible; tested on re-run.
  - `backend/api/scanner.py` with:
    - `POST /api/scanner/test-frames` (multipart: frame JPEG + JSON metadata). **Idempotent:** on `frame_hash` UNIQUE conflict, return existing record with 200, do not overwrite ground-truth or other fields.
    - `GET /api/scanner/test-frames` (list; filters: annotated/unannotated/excluded; pagination)
    - `GET /api/scanner/test-frames/{id}/image` (raw frame)
    - `PATCH /api/scanner/test-frames/{id}` (set ground truth, excluded, notes). `ground_truth_json` reuses `Quad` shape.
    - `DELETE /api/scanner/test-frames/{id}` (frame row + JPEG file both removed)
  - Storage: `backend/storage.py::save_scanner_test_frame(hash, jpeg_bytes)` → `data/scanner_test_set/{hash}.jpg`
  - **Retention policy** (per D2 + auto-applied): invoked on each new frame. Cap from settings (default 1000). Eviction order: LRU on `captured_at`, where `excluded = 0 AND ground_truth_json IS NULL`. Annotated frames are sticky. If the entire set is annotated and over cap, log a warning and accept new frames anyway (don't evict annotated data without explicit user action).
  - `detector_name` field stores `name@version` format (e.g., `classical@2`) to survive detector evolution.
- Frontend:
  - `lib/api.ts`: scanner test-frame client functions
  - `useScanner` on accept (and on retake / manual crop): downscale captured raw frame to max 1280px long edge, JPEG q=85, POST to backend with `corners_at_capture`, `user_action`, `detector_name`. **Fire-and-forget** — does not block the scan accept flow; failures logged but not surfaced.
  - **Note:** capture both `accepted` and `retaken`/`manual_crop` actions — the failure cases are the most valuable signal. Per D2, ground truth on `retaken` frames is added later via the lab.

### 1.6 Telemetry & rollout
- Log per-detection: detector name, timing, corners-or-null, confidence
- Feature flag `RECEIPTORY_SCANNER_DETECTOR` env var (`classical_improved` | `legacy_scanic`) so we can roll back if real-world regression appears
- Default to `classical_improved`

### Sprint 1 deliverables checklist
- [ ] Detection worker scaffolding (D8) + `WorkerDetector` proxy
- [ ] `Detector` interface + `useDetector` hook (D6) + `ClassicalDetector` with preprocessing, scoring, rejects
- [ ] Multi-pass scanic candidate generation (D3)
- [ ] `TemporalSmoother` integrated into `CameraViewfinder`
- [ ] `scanner_test_frames` table + REST endpoints + storage with LRU eviction policy
- [ ] Raw frame upload (fire-and-forget) on every scan action (accept / retake / manual crop)
- [ ] Existing scanner page using new pipeline; legacy path behind env flag
- [ ] Tests:
  - `preprocess.test.ts` (golden image fixtures: white-on-white, dark-on-dark, glare, busy background; α=0/β=0/both edge cases; pure grayscale input)
  - `scoring.test.ts` (each hard-reject criterion isolated + combinations; weight extremes — single nonzero, all zero; empty candidate list returns null)
  - `smoother.test.ts` (empty buffer first call; alternating null/valid sequences; outlier rejection above threshold; auto-capture trigger reaches threshold; near-stable but never crosses)
  - Backend `test_scanner_api.py` (POST happy path; idempotent hash conflict returns existing; PATCH ground truth; DELETE removes file + row; retention triggers when over cap; retention skips annotated; no annotated frames + over cap warns + accepts; FK on document delete sets `document_id=NULL` and frame survives; migration reversible / idempotent on re-run)
  - **Regression test (iron rule):** `test_scanner_legacy_flag.py` — `RECEIPTORY_SCANNER_DETECTOR=legacy_scanic` produces legacy scanic-only behavior with no preprocessing, no scoring, no smoothing. The rollback path must not silently break.
  - Worker integration smoke test: main thread → worker → result roundtrip with a fixture image

---

## Sprint 2 — Scanner Lab (admin UI)

**Duration estimate:** 5–7 days
**Ships:** new admin tab; user can browse the accumulating test set, annotate ground truth, A/B-test detector configs, save profiles, activate a profile back into the live scanner.

### 2.1 Page scaffolding
- Add `frontend/src/pages/admin/ScannerLabPage.tsx` and route `/admin/scanner-lab`
- Sidebar entry under existing admin section
- Three sub-tabs: **Live**, **Test Set**, **Profiles**

### 2.2 Live tab
- Camera + overlay. Multiple detectors selectable simultaneously, each rendered in a distinct color
- Per-detector parameter panel (rendered from `Detector.schema`) with sliders/toggles
- Live numeric readouts: timing, confidence, stability, candidate count
- "Capture to test set" button (independent of normal scan flow, lets user deliberately seed hard cases)

### 2.3 Test Set tab
- Grid of thumbnails. Filters: annotated / unannotated / excluded / by user_action / date range
- **Frame inspector** (click thumbnail): shows raw frame; overlays for ground truth, corners-at-capture, and any currently-selected detector's prediction; per-candidate score breakdown table
- **Ground-truth annotator:** drag 4 corners on the frame; save to `ground_truth_json`. Keyboard shortcuts (`a` accept current detector's prediction as truth, `r` reset, `x` exclude)
- Bulk actions: exclude, delete, export

### 2.4 Profiles tab
- List of saved profiles. Each profile = `{ name, detector, config, createdAt, lastEvalMetrics }`
- "New profile from current Live config" button
- **Eval runner** (per D7):
  - "Run against test set" kicks off browser-side eval over all annotated frames in a Web Worker (reuse the detection-worker scaffolding from Sprint 1.0)
  - Progress bar with per-frame updates; cancel button mid-run; idempotent state cleanup on cancel
  - Results table per metric (D3): hit rate, mean/median IoU, false-quad rate, null rate, median inference time
  - **Every eval result tagged with device metadata** (per D4): `{ userAgent, ep: 'webgpu' | 'wasm-simd' | 'wasm', cpuCores, runAt }`. Stored alongside the profile. Lab UI groups results by device-class so cross-device timing isn't compared incorrectly.
- Side-by-side comparison view: pick 2 profiles, see metrics diff + per-frame win/loss thumbnails. Filter by device-class so timings are honest.
- "Activate" button: writes profile to `settings.scanner_active_profile`. **Banner appears (per D5):** "Reload any open scanner page to apply the new profile." No automatic cross-tab notification.

### 2.5 Backend additions
- `GET/POST/PATCH/DELETE /api/scanner/profiles`
- `GET/PUT /api/scanner/active-profile` (reads/writes settings row)

### Sprint 2 deliverables checklist
- [ ] Admin sub-route + three tabs scaffolded
- [ ] Live tab with multi-detector overlay + per-detector param controls
- [ ] Test Set tab with grid, filters, inspector, ground-truth annotator
- [ ] Profiles tab with save / eval (in worker, cancellable) / compare / activate
- [ ] Eval results tagged with `{userAgent, ep, cpuCores}` and grouped by device-class (D4)
- [ ] "Reload to apply" banner after activation (D5)
- [ ] Profile + active-profile API + settings integration
- [ ] Live scanner reads `scanner_active_profile` on mount
- [ ] Tests (per D7):
  - **Backend retention edge cases:** cap=0; all frames annotated and over cap (warn, accept); FK on document delete preserves frame; oversize JPEG rejected; PATCH `excluded` toggle; PATCH `ground_truth_json` with `Quad` shape validation
  - **Eval-runner contract tests (frontend):** IoU computation correctness on known quads; metric aggregation across N frames; cancel mid-run leaves no pending state; single-frame run; empty annotated set returns "no frames"; malformed `ground_truth_json` skipped with warning, not crash; device tag fields populated correctly
  - **One E2E:** annotate a frame in lab → save → reload page → ground truth still present and rendered

---

## Sprint 3 — ML detector

**Duration estimate:** 7–10 days
**Ships:** ML-based detector evaluable in the lab; decision to ship as default / hybrid / not at all is made *with data* from the accumulated test set.

### 3.1 Model selection (timeboxed: 1–2 days)
- Survey: DocAligner, MBD, MIDV-trained corner regressors, tiny U-Net segmentation models
- Constraints: MIT/Apache, ≤5 MB INT8, ≤256×256 input, runs in onnxruntime-web
- Pick 1–2 candidates; convert to ONNX if needed; INT8 quantize
- Document choice + rationale in this plan as an addendum

### 3.2 Runtime integration
- Add `onnxruntime-web` dependency
- `frontend/src/lib/scanner/ml-runtime.ts`: lazy-load runtime, pick best EP (WebGPU > WASM-SIMD-threads > WASM)
- Self-host model weights at `frontend/public/models/`; fingerprint URL; long-cache headers
- Cold-start hidden behind existing scanner "loading" phase

### 3.3 MLDetector implementation
- `frontend/src/lib/scanner/ml-detector.ts` implementing `Detector`
- For corner-regression model: input → letterbox to 256×256 → infer → 8 outputs → unletterbox to source dims
- For segmentation model: input → infer → mask → connected components → min-area rotated rectangle → 4 corners
- Returns confidence from model output (regression: 1 − corner uncertainty if model emits it; segmentation: mean mask probability inside fitted quad)

### 3.4 HybridDetector
- `frontend/src/lib/scanner/hybrid-detector.ts`
- Strategy: run classical first (fast). If confidence < threshold OR null for >K consecutive frames, run ML on next frame. If ML confidence > classical, swap. Configurable.
- Same `Detector` interface so it's just another option in the lab

### 3.5 Evaluation gate
- Run all three detectors against the accumulated test set in the lab
- Hard ship criteria for ML-as-default: ≥10pp hit-rate improvement over classical-improved, **p50 inference ≤300ms on a phone-tagged eval run** (per D4 — desktop-tagged runs do not satisfy this gate; the lab eval result must show `ep: 'wasm-simd'` or `'wasm'` and a mobile `userAgent`)
- Soft criteria: false-quad rate ≤ classical's
- If ML wins → activate hybrid profile (classical + ML fallback). If ML loses → keep classical profile, ship ML as opt-in only.

### 3.6 Polish
- Lazy-load runtime + model only on scanner page or scanner-lab page
- Loading state for first-ever ML detection (warmup)
- Telemetry: which detector served each scan, timing distribution, fallback rate

### Sprint 3 deliverables checklist
- [ ] Model selection documented
- [ ] `MLDetector` and `HybridDetector` implementing the interface
- [ ] onnxruntime-web wired with WebGPU/WASM fallback chain
- [ ] Evaluation report against accumulated test set
- [ ] Decision on default profile, written into settings

---

## Cross-cutting risks & mitigations

| Risk | Mitigation |
|---|---|
| Test-set bias: every auto-saved frame is one the *current* detector handled, so we never see the hardest failures | Also save `retaken` and `manual_crop` actions — those are the failure cases. Flag them in the lab UI. |
| Storage growth | Cap at 1000 frames; auto-prune oldest unannotated. Setting is configurable. |
| Tuning never gets deployed | "Activate" button writes profile back to live settings. No separate "save vs deploy" gap. |
| Mobile bandwidth | Frames downscaled to 1280px long edge before upload (~200–500 KB). Acceptable on home WiFi. |
| Yak-shaving in the lab | Sprint 1 ships a real improvement before Sprint 2 even starts. Lab is built on top of working code. |
| ML perf cliff on old Android | Hybrid detector keeps classical as primary path; ML only on low-confidence frames. WASM fallback if WebGPU absent. |
| ML model license / quality unknown | Sprint 3.1 is a timeboxed survey; if no acceptable off-the-shelf model exists, abort Sprint 3 and ship classical-only with the lab as the lasting artifact. |

---

## Resolved during eng review

1. **Frame upload at every retake** → keep all, fire-and-forget. (Auto-applied)
2. **Browser-side eval in a Web Worker from day one** → Yes. Reuses Sprint 1.0 detection-worker scaffolding. (D7 + D8)
3. **Profile schema versioning** → `detector_name` uses `name@version` format; profile ties config to a specific detector version. (Auto-applied)
4. **`legacy_scanic` rollback path** → kept as env-flag-only fallback through Sprint 2; iron-rule regression test ensures it doesn't silently break. Reconsider removal in Sprint 3 only if classical-improved has been stable for ≥30 days.

## Unresolved decisions (may revisit)

1. **Ground-truth annotation UX on phone.** The drag-4-corners interaction will be fiddly on small screens. Plan currently relies on the admin page being responsive and the user occasionally annotating on desktop. If annotation throughput turns out to be the bottleneck for Sprint 3 evaluation, revisit (e.g., a "tap each corner in order" flow for mobile).
2. **Cross-tab profile sync.** Plan documents "reload to apply." If the laptop-tunes-phone-scans workflow turns out to be common, revisit — add 30s polling on `/api/scanner/active-profile`.
3. **Test-set bias toward Sprint 1's detector.** Lab-only annotation accepted (D2). If Sprint 3 ML evaluation produces suspiciously similar hit rates to Sprint 1, this is the first thing to suspect — re-annotate a random sample blindly and compare.

---

## Worktree parallelization

**Sprint 1 internal lanes:**

| Step | Modules touched | Depends on |
|---|---|---|
| 1.0 Detection worker | `frontend/src/workers/`, `lib/scanner/` (proxy) | — |
| 1.1 Detector interface + `useDetector` | `lib/scanner/` | 1.0 (proxy contract) |
| 1.2 Preprocessing | `lib/scanner/` | 1.1 |
| 1.3 Multi-pass scanic + scoring | `lib/scanner/` | 1.2 |
| 1.4 TemporalSmoother | `lib/scanner/` | 1.1 |
| 1.5 Test-set capture | `backend/api/`, `backend/storage.py`, migration, `frontend/lib/api.ts` | — (independent of 1.0–1.4) |
| 1.6 Telemetry + flag rollout | `frontend/pages/`, settings | 1.1–1.5 |

**Lanes:**
- **Lane A** (frontend lib, sequential): 1.0 → 1.1 → 1.2 → 1.3 → 1.4 (1.4 can branch from 1.1, but shares files with 1.2/1.3 → safer sequential)
- **Lane B** (backend + light frontend, fully independent): 1.5
- Final integration: 1.6 after both lanes land

**Execution:** Launch A and B in parallel worktrees. Merge both. Then 1.6.

**Conflict flag:** none — A is `frontend/src/lib/scanner/` + `workers/`, B is `backend/` + `frontend/src/lib/api.ts`. Disjoint.

**Sprint 2 and 3** are sequential after Sprint 1; internal parallelization within each is limited because the lab UI (Sprint 2) shares state across tabs and the ML pipeline (Sprint 3) is one cohesive integration.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---|---|---|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | skipped (Codex CLI unavailable in env) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 7 issues raised, 9 decisions captured (D1–D9), 8 auto-applied items, 1 TODO captured, 3 unresolved decisions noted |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | not run |
| DX Review | `/plan-devex-review` | DX gaps | 0 | — | not run |

- **UNRESOLVED:** 3 (annotation UX on phone; cross-tab profile sync; test-set bias) — captured in plan as "may revisit" rather than blocking
- **VERDICT:** ENG CLEARED — ready to implement Sprint 1

