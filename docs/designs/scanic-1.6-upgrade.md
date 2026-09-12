# Design: scanic 1.0.6 → 1.6.0, detection held to 1.0.6

Written 2026-09-11 after a /investigate on the Naya receipt (document #296)
Branch: fix/scanner-scanic-1.6 (stacked on feat/scanner-manual-corners, PR #36)
Status: IMPLEMENTED. On-device (S26 Ultra, 2026-09-11): two Naya captures, seam depth −0.3,
both boxes on the receipt. The /review changes below (no-box crop, degenerate-quad guard) came
after that test.
Rollback point: git tag `rollback/pre-scanic-1.6` (133afea), image `receiptory-receiptory:rollback-pre-scanic-1.6` (fa097624a56e)

## Problem

Scans of narrow receipts carry a lattice of thin dark lines: near-vertical stripes plus a
slanted set. On the Naya receipt (485×2132) they are a 7.57px lattice, ~10 grey levels deep,
amplified by the review screen's `contrast(1.4)` enhancement.

## Root cause

scanic 1.0.6's `extractDocument` (`warpTransform`, dist `scanic.js:1197-1306`) does not warp
per pixel. It splits the output into a 64×64 grid, cuts each cell into two triangles and draws
all 8,192 with `clip()` + `setTransform()` + `drawImage()`. Anti-aliased clip edges leave
partially transparent pixels along every seam, and `toBlob("image/jpeg")` flattens transparency
onto black.

It hides this by pushing each triangle's vertices 1px out from its centroid. On a thin cell
that barely moves the long edges: Naya's cells are 7.6×33.3px, and the vertical and diagonal
edges get 0.11px of overlap (horizontal edges get 0.91px). The seams sit exactly on those two
edge types. Severity tracks receipt narrowness across every app scan on record:

| Scan | Cell width | Seam depth (grey levels) |
|---|---|---|
| Naya #296 | 7.6px | −10.0 |
| Toy store #290 | 12.5px | −3.4 |
| Tzamtzam #291 | 16.0px | −1.4 |
| Garage #292 | 24.4px | −0.6 |

Verified by geometry (predicted period W/64 = 7.578px, measured 7.574px; predicted diagonal
12.8°, measured 13.2°) and by a Chromium repro with the exact Naya corners: 17.9% of output
pixels partially transparent, seam depths within 0.3 grey levels of the production scan.

## Decision

Upgrade to scanic 1.6.0, whose extract is a per-pixel bilinear inverse map ("no seam
artifacts", per its README). In the repro: 0 partially transparent pixels, seam depth −0.04,
2.8× faster.

Rejected alternatives:
- **Own warp on 1.0.6.** Same result, but code we maintain and throw away at the next upgrade.
- **Two scanic versions (1.0.6 detect, 1.6.0 crop).** Two copies of one library and a
  permanent import trap.

## What 1.6.0 changes in detection, and why three options are pinned

1.6.0's detector is not a drop-in. By default it derives Canny thresholds from each frame's
gradient histogram, runs cascade passes with heavier dilation, pools candidates across passes
and ranks them by a confidence score that gives 22% of its weight to area relative to 40% of
the frame. On the Naya frame it boxed the wood grain; tightening `minRightAngleScore` moved it
to a different wrong quad spanning the desk. Candidate corners are not exposed (debug output
carries metrics only), so we cannot re-rank them ourselves without patching scanic.

1.0.6 used fixed thresholds 75/200, one pass, and took the largest contour. Holding 1.6.0 to
that edge map restores parity:

```ts
// opencv-loader.ts: SCANIC_DETECTION_OPTIONS
{ lowThreshold: 75, highThreshold: 200, enableDetectionCascade: false }
// classical-detector.ts: scanicDetectOptions(p)
{ mode: "detect", maxDocumentAspectRatio: p.maxAspect }   // 12; scanic's default is 8
```

`maxDocumentAspectRatio` follows our own gate because 1.6.0 ranks every valid candidate above
an invalid one, and 8:1 would mark a long restaurant slip invalid.

This fixes the edge map and the single pass. It does not make selection identical: 1.6.0 still
scores up to 12 contours (valid first, then confidence) and refines corners its own way, where
1.0.6 took the largest contour. Accepted frames over the corpus: 1.0.6 {4, 9, 10, 26, 37, 42,
43, 44, 46, 47}, pinned 1.6.0 {4, 9, 10, 37, 42, 43, 44, 47}. #46 is a recent 4K capture whose
1.0.6 box was a near-triangle (two corners ~10px apart) and was hand-corrected at capture, so
losing it is likely no loss. Forcing `maxCandidateContours: 1` does not restore the 1.0.6 set
either (0 correct / 2 wrong / 29 no box); it loses #4.

## Changes from /review (2026-09-11)

- **No box, no guess.** With no corners, `extractAndEnhance` used to run scanic's own
  full-frame detect-and-crop: unpreprocessed, ungated, and filed while review showed a
  different quad. Under 1.6.0 that crop collapsed to a speck (0.1% of the frame on #1, #12 and
  #46; 17 → 23 of 46 frames under 5%). It was already unreliable on 1.0.6. Now
  `ScannerPage.handleCapture` crops the inset quad review draws, and `extractAndEnhance` never
  detects. Verified: a no-box 4K capture crops 1728×3072, the on-screen box.
- **No black pages.** 1.6.0's extract returns a solid black page with `success: true` for
  collinear or coincident corners (its singular homography becomes NaN, and every pixel truncates to 0).
  `isWarpableQuad` (geometry.ts) rejects non-convex, flat-cornered or sub-8px-sided quads first,
  and the whole frame is returned, the existing failed-warp policy. Verified for collinear,
  three-on-an-edge and coincident corners.
- **Real types.** The hand-written `src/types/scanic.d.ts` from the first scanner commit
  shadowed scanic's shipped types, so the pinned options were never type-checked (a
  misspelling compiled). Removed. The pinned options use `satisfies DetectionOptions` (a typo is
  now TS2561), and the crop calls the exported `extractDocument`, which is what
  `Scanner#extract` wraps and which 1.6.0's typings do declare.
- **Init semantics.** 1.6.0's `initialize()` swallows a WASM load failure and runs its JS
  pipeline, so a WASM failure is a slowdown rather than an error badge. The init-latch handling
  stays for anything else that throws.
- **TODO closed.** "Scanic WASM instance leaks on every scanner close" assumed one WASM heap
  per `Scanner`. Both versions hold a single module-level instance (1.0.6
  `if (wasm !== void 0) return wasm`; 1.6.0 caches its init promise), so nothing leaks.

Measured by the /review performance pass (headless Chromium, no GPU): crop of a narrow receipt
306 → 116ms and of a full page 654 → 324ms; live detect on a 450×800 frame 36.7 → 26.2ms;
main bundle +5.8kB gzip. The crop does allocate about 66MB more transient memory at 4K (a
full-frame pixel copy plus the output buffer). That is not a problem on the S26 Ultra so far;
if multi-page sessions show pressure, pass the ImageData straight to `extractDocument` and
build the full-frame canvas only on fallback.

## Evidence

Measured in headless Chromium with the app's own `ClassicalDetector` and `extractAndEnhance`
(real `opencv-loader`, built once per scanic version), mirroring `ScannerLabPage.runEval`:
downscale to `DETECTION_MAX_EDGE`, default params, `quadIoU` on angle-ordered quads, hit at
IoU ≥ 0.85. Harness faithfulness check: 1.0.6's corners match the capture-time corners on the
four unedited recent frames at IoU 0.99–1.00.

Labelled corpus, 32 frames:

| Config | Correct box | Wrong box (IoU < 0.5) | No box |
|---|---|---|---|
| 1.0.6 | 1 | 2 | 28 |
| 1.6.0 defaults | 3 | 11 | 17 |
| 1.6.0 defaults, fall back from pinned | 3 | 11 | 17 |
| **1.6.0 pinned (shipped)** | **1** | **2** | **29** |

1.6.0's defaults look better on hit rate (9.4% vs 3.1%) only because they accept far more
boxes, most of them wrong. For this app a wrong box is worse than none: no box leads to the
manual-corners prompt. Pinned trades frame #26 (lost) for #4 (0.80 → 0.94).

Recent 4K captures (#42, 43, 44, 47; IoU against capture-time corners): 1.0.6 1.00/0.99/0.99/0.99,
pinned 0.98/0.96/0.95/0.99; overlays are visually indistinguishable. Median detect time 61ms
(1.0.6) vs 63ms (pinned) on desktop Chromium.

App crop path (`extractAndEnhance`, synthetic 4K frame, Naya corners): 1.0.6 184,601 of
1,034,020 pixels partially transparent, seam depth −9.0; 1.6.0 zero, −0.04.

Caveats: the labelled corpus is mostly July frames, and our own area gate rejects most of them
under every config, so it is a weak instrument for detector quality. The on-device verdict
decides.

## Guard rails

- `opencv-loader.test.ts` fails if the pinned options leave the Scanner constructor, and fails
  if the installed scanic is not 1.6.0 (a tripwire for re-measuring before any bump).
- `classical-detector.test.ts` pins `maxDocumentAspectRatio` to `maxAspect`.
- `geometry.test.ts` covers `isWarpableQuad` on the three black-page shapes, a bow-tie, a tiny
  side, non-finite corners and real crops.
- The pinned options are type-checked against scanic's `DetectionOptions`.
- `package.json` pins `scanic` to exactly `1.6.0`. Before any bump, repeat the Evidence
  measurement: the harness was a throwaway (a Vite IIFE build of `ClassicalDetector` per
  scanic version, driven by Playwright over `data/scanner_test_set`) and is not in the repo.
- scanic 1.6.0 declares `engines.node >= 22`; the Docker frontend stage is `node:20-slim`.
  The image builds (verified 2026-09-11, image e1417efa5e37). npm does not enforce
  `engines` without `engine-strict`, and scanic runs in the browser, not in Node.
- Canary for a built image: `minCascadeTriggerConfidence` exists only in scanic 1.6 and
  `should be lower than highThreshold` only in 1.0.6
  (`docker run --rm --entrypoint sh <image> -c 'grep -l <string> /app/frontend/dist/assets/*.js'`).

## Rollback

Instant, no rebuild (about 8s of downtime):

```bash
docker tag receiptory-receiptory:rollback-pre-scanic-1.6 receiptory-receiptory:latest
docker compose up -d --no-build receiptory
```

Code: `git checkout rollback/pre-scanic-1.6` (or `feat/scanner-manual-corners`), then
`docker compose build receiptory && docker compose up -d receiptory`. No schema or settings
change is involved: `scanner_active_config` params are unchanged, and unknown keys are ignored
by both versions.
