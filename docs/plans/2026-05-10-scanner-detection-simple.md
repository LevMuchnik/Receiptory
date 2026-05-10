# Scanner Detection — Simple Plan

**Goal:** Receipt boundary detection often fails (locks onto text blocks, returns weird shapes, blind on white-on-white). I want to fix it with classical improvements and try an ML option, with a small lab UI to compare them and tune parameters on my own receipts.

**Approach:** Three short sprints. Everything runs in the browser; backend just stores raw frames and the active config. Single user, single device per session — no production-grade hardening.

**Supersedes:** `2026-05-10-scanner-detection-overhaul.md` (kept for reference; that plan is over-engineered for personal use).

---

## Sprint 1 — Classical detection improvements + raw-frame capture

**Duration:** ~4 days

### 1.1 Detector interface

`frontend/src/lib/scanner/detector.ts`

```ts
export type Pt = { x: number; y: number };
export type Quad = { topLeft: Pt; topRight: Pt; bottomRight: Pt; bottomLeft: Pt };

export interface DetectionResult {
  corners: Quad | null;
  score: number;            // detector-internal; meaningful only within the same detector
  candidates?: { quad: Quad; score: number }[];   // for the lab inspector
  timingMs: number;
}

export interface Detector {
  readonly name: string;
  detect(image: ImageData, params: any): Promise<DetectionResult>;
  getDefaultParams(): any;
}
```

`score` is intentionally not normalized across detectors. The lab compares them visually, not numerically.

### 1.2 ClassicalDetector

`frontend/src/lib/scanner/classical-detector.ts`

- Wraps `scanic` (existing dependency)
- Preprocessing pipeline (configurable on/off):
  - Shadow normalization: grayscale → divide by stack-blurred copy (kernel ≈ width / 8). Stack blur, not Gaussian — fast and visually equivalent
  - Saturation mask: HSV S-channel inverted as paper-likelihood prior
  - Compose: weighted sum of the two, fed into scanic
- Scoring: pick the best quad scanic returns by:
  - Hard rejects: area fraction outside [0.12, 0.95]; aspect ratio outside [0.4, 12]; min interior angle <50° or >130°
  - Score = `w_area·area + w_convex·convexity + w_uniform·interior_uniformity − w_text·interior_text_density`
- Tunable params: shadow norm on/off, saturation prior on/off, blur kernel size, the four scoring weights, the hard-reject thresholds
- Runs on main thread. If detection time becomes a problem on the phone, move to a worker then.

### 1.3 TemporalSmoother

`frontend/src/lib/scanner/smoother.ts`

- Ring buffer of last 10 detections
- Per-corner EMA, α = 0.4
- Drop frames whose corners drift >15% of frame diagonal from the EMA (only after 3 detections accumulated; no rejection during warm-up)
- `stability ∈ [0,1]` based on max corner drift over the buffer
- `shouldAutoCapture()`: stability > 0.9 sustained for ~700 ms (time-based, not frame-count, so it feels the same regardless of detection rate)

### 1.4 Wire it into the scanner

- `useScanner` and `CameraViewfinder` consume a `Detector` instance plus `TemporalSmoother`
- Active config read from settings on mount: `scanner_active_config = { detector: 'classical', params: {...} }`. Default is classical with sensible params.
- On scan accept: existing behavior, plus fire-and-forget upload of the raw camera frame to the test set (Sprint 1.5)

### 1.5 Test-set capture

Backend:

```sql
CREATE TABLE scanner_test_frames (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  frame_path TEXT NOT NULL,           -- relative path under data/scanner_test_set/
  captured_at TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  detector_name TEXT,                  -- which detector ran; null if manual capture
  corners_at_capture_json TEXT,        -- detector output at capture time
  ground_truth_json TEXT,              -- annotated later in the lab
  notes TEXT
);
```

- File: `data/scanner_test_set/{yyyy-mm-dd}/{timestamp}-{shortid}.jpg`
- API:
  - `POST /api/scanner/test-frames` (multipart: JPEG + metadata)
  - `GET /api/scanner/test-frames` (list)
  - `GET /api/scanner/test-frames/{id}/image` (gated by existing session auth)
  - `PATCH /api/scanner/test-frames/{id}` (set ground truth, notes)
  - `DELETE /api/scanner/test-frames/{id}`
- No retention cap. If disk fills up I'll delete frames manually from the lab. The whole NAS is the user's anyway.
- Frame upload from `useScanner`: downscale to 1280px long edge, JPEG q=85, fire-and-forget

### 1.6 Tests

- `classical-detector.test.ts`: golden image fixtures (white-on-white, glare, busy background) — preprocessing produces expected output; scoring picks the right candidate
- `smoother.test.ts`: warm-up behavior, EMA convergence, outlier rejection after warm-up, auto-capture timing
- `test_scanner_api.py`: POST/GET/PATCH/DELETE roundtrip; image endpoint requires session auth

### Deliverables

- [ ] `Detector` interface + `ClassicalDetector` with preprocessing and scoring
- [ ] `TemporalSmoother` integrated into camera viewfinder
- [ ] `scanner_test_frames` table + REST endpoints + storage
- [ ] Raw frame upload on every scan
- [ ] Active-config setting; live scanner reads it on mount
- [ ] Tests above

---

## Sprint 2 — Scanner Lab

**Duration:** ~3 days

A single page at `/admin/scanner-lab`. One route, three sections on the same screen.

### 2.1 Capture / pick a frame

- Camera viewfinder (reuse `CameraViewfinder` component) with an "Add to test set" button — captures the current frame, no detection required
- "Or pick from test set" — thumbnail grid of saved frames, click to load

### 2.2 Compare detectors

- Two side-by-side panels. Each panel:
  - Detector dropdown (at first: just `classical`; Sprint 3 adds `ml`)
  - Parameter sliders/toggles built from the detector's default params (rendered with a tiny generic form — text input for numbers, checkbox for booleans, no `ParamSchema` discriminated union)
  - "Detect" button → runs the detector on the loaded frame, draws corners overlaid
  - Numeric readouts: score, timing, candidate count
  - Per-candidate quads visualized in a faded color, the chosen one highlighted

This is the main experimentation surface. Drag a slider, click Detect, see what changes. No background eval, no test-set runs — just one frame at a time, both detectors at once.

### 2.3 Annotate ground truth

- On the loaded frame, drag 4 corners to mark "this is what should have been detected"
- Save to `ground_truth_json`
- Used later (Sprint 3) when comparing detectors against ground truth

### 2.4 Save and activate config

- "Save as active config" button on either panel writes `{detector, params}` to `settings.scanner_active_config`
- Banner: "Reload the scanner page to apply"
- One active config at a time. No named profiles, no history. If I want to remember an old config I'll write it down.

### 2.5 Backend

- `GET/PUT /api/scanner/active-config` (reads/writes settings row)
- Test-frame endpoints already exist from Sprint 1

### 2.6 Tests

- E2E: load lab → capture frame → detect with classical → adjust a param → re-detect → save as active → reload scanner → new params apply
- Backend: active-config GET/PUT roundtrip

### Deliverables

- [ ] `/admin/scanner-lab` route + page
- [ ] Frame capture + test-set picker
- [ ] Side-by-side detector panels with sliders
- [ ] Ground-truth annotation UI
- [ ] Save-as-active-config + banner
- [ ] Tests above

---

## Sprint 3 — ML detector

**Duration:** ~5 days

### 3.1 Pick a model (timeboxed: 1 day)

- Survey: DocAligner, MBD, MIDV-trained corner-regression models, tiny U-Nets
- Constraints: MIT/Apache, ≤5 MB INT8, ≤256×256 input, runs in onnxruntime-web
- If nothing usable exists off the shelf: stop, ship classical-only, document why

### 3.2 Runtime + detector

- Add `onnxruntime-web` dependency
- `frontend/src/lib/scanner/ml-detector.ts` implementing `Detector`:
  - Lazy-load the model (cold start hidden behind the lab/scanner loading state)
  - Pick best execution provider: WebGPU → WASM-SIMD → WASM
  - For corner regression: letterbox to model input → infer → unletterbox 8 outputs
  - For segmentation: infer mask → fit min-area rotated rectangle
- Self-host weights at `frontend/public/models/`, fingerprinted URL

### 3.3 Compare in the lab

- Add `ml` to the detector dropdown in Sprint 2's lab
- Run classical and ML side by side on saved frames. Eyeball which is better on hard cases.

### 3.4 Optional: simple eval against ground-truth frames

If I've annotated some frames, add a "Run on all annotated frames" button per panel that reports:
- Hit rate (IoU ≥ 0.85)
- Median IoU
- Median timing

Nothing fancy. Just iterate, run, sum. Web Worker only if it freezes the UI.

### 3.5 Deliverables

- [ ] Model selection documented
- [ ] `MLDetector` runs in the lab
- [ ] Decision based on visual comparison + (optionally) ground-truth eval: which detector becomes the active config

---

## Notes on simplifications taken

- **No Web Worker.** Detection runs on the main thread. If it stutters on the phone, move it later. Don't optimize for a problem that hasn't happened.
- **No HybridDetector.** Two detectors, pick one as active. If I want to try both I switch in the lab.
- **No multi-pass scanic.** Single pass on the preprocessed input. If scoring + temporal smoothing isn't enough, revisit.
- **No calibrated confidence.** Score is detector-internal. Comparison is by eye and by ground-truth IoU, not by confidence number.
- **No detector versioning, no profile schemas.** Active config is one JSON blob. If I change a detector's params I lose the old config — that's fine, I'll write down the values I cared about.
- **No retention policy.** Manually delete frames from the lab if disk fills.
- **No idempotency wrappers.** UNIQUE constraint errors bubble up; I'll see them in logs.
- **No env-var rollback flag, no regression test for it.** `git revert` is the rollback.
- **No device-tagging eval results.** I run eval on whichever device I'm on. If a result looks suspicious I'll re-run on the phone.
- **One lab page, three sections.** Not three tabs, not a profile manager, not a comparison history.

---

## Future hardening (only if needed)

If this plan ships and a real problem appears, these are the upgrades worth considering:

- **Web Worker for detection** if main thread stutters on the phone. Use Transferable Objects when posting `ImageData` to avoid 10–30 ms clone cost.
- **Hash raw pixel bytes, not JPEG bytes**, if duplicate test-set entries become a problem (JPEG re-encoding is non-deterministic).
- **Frame orientation normalization** if portrait-mode captures fail more than landscape. Apply rotation based on `screen.orientation` before detection.
- **Calibrated cross-detector confidence** if I want a real hybrid. Isotonic regression from raw_score → expected IoU on annotated frames.
- **HybridDetector with classical primary + ML fallback** if classical is mostly fine but ML rescues hard cases.
- **Multi-pass scanic** (raw, shadow-normed, saturation-masked, dedupe by IoU) if single-pass + scoring still misses hard cases.
- **Retention cap with LRU eviction** if disk fills faster than I can manually prune.
- **Named profiles + history** if I find myself toggling between configs for kitchen vs office light.
- **Test set as ML fine-tuning corpus** — see `TODOS.md`.

---

## What already exists (reused, not rebuilt)

- `scanic` integration in `frontend/src/lib/opencv-loader.ts`
- `useCamera`, `CameraViewfinder` — refactored to consume a `Detector`, not rewritten
- Existing settings table — active config is a new key
- Backend storage primitives in `backend/storage.py`
- Session auth gate from existing API endpoints
