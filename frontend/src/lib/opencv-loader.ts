// Scanner engine using Scanic (lightweight document scanner, ~100KB WASM)

import { Scanner, extractDocument, type DetectionOptions } from "scanic";

import { imageDataToCanvas } from "./scanner/canvas-utils";
import type { Quad } from "./scanner/detector";
import { isWarpableQuad } from "./scanner/geometry";

let scanner: Scanner | null = null;
let initPromise: Promise<void> | null = null;

/**
 * Detection options that hold scanic 1.6 to the edge map 1.0.6 produced: fixed
 * Canny thresholds and a single pass. The live detector (`classical-detector`)
 * is the only scanic detect call; `extractAndEnhance` never detects.
 *
 * Why 1.6 at all: 1.0.6's extract drew the warp as 8,192 anti-aliased triangles,
 * and the seams came out as dark lines on narrow receipts (a 7.6px lattice on
 * the 2026-09-11 Naya scan). 1.6's extract is a per-pixel inverse map.
 *
 * Why these three: 1.6's defaults derive Canny thresholds from each frame's
 * gradient histogram and add cascade passes that dilate edges harder, then rank
 * the pooled candidates by a score weighted toward large quads. On a narrow
 * receipt on a wood desk that picks the wood grain. Over the 46-frame scanner
 * corpus, defaults produced 11 wrong boxes on the 32 labelled frames against
 * 1.0.6's 2. With these options it is 2, and the recent 4K receipt captures land
 * on the receipt. Measured with the app's own ClassicalDetector; the numbers
 * and the rollback procedure are in docs/designs/scanic-1.6-upgrade.md.
 *
 * Not full parity: 1.6 still ranks up to 12 candidate contours and refines
 * corners its own way, where 1.0.6 took the largest contour. The accepted-frame
 * sets differ by a frame or two; the design record lists which.
 *
 * `maxDocumentAspectRatio` is NOT here: it tracks `ClassicalParams.maxAspect`
 * and is passed per call (see `scanicDetectOptions`).
 */
export const SCANIC_DETECTION_OPTIONS = {
  lowThreshold: 75,
  highThreshold: 200,
  enableDetectionCascade: false,
} as const satisfies DetectionOptions;

/**
 * Initialize the Scanic engine. Safe to call repeatedly; concurrent callers
 * share one in-flight promise.
 *
 * The module-level `scanner` is published only AFTER `initialize()` resolves,
 * and `initPromise` is cleared on rejection. An earlier version assigned
 * `scanner` before awaiting `initialize()`, so a single init failure left a
 * half-built instance behind: `if (scanner) return` short-circuited every later
 * call and the failure latched permanently with no way to retry.
 *
 * Rejections propagate to the caller — classical-detector's catch depends on
 * that to populate DetectionResult.error.
 *
 * Since scanic 1.6, `initialize()` itself never rejects: it swallows a WASM load
 * failure and runs the JS pipeline instead, so a WASM failure is a slowdown, not
 * an error badge. The latch handling above still matters for anything else that
 * throws here (the constructor, a future scanic that rejects again), and its
 * tests drive it with a fake that does reject.
 */
export async function initScanner(): Promise<void> {
  if (scanner) return;
  if (initPromise) return initPromise;

  const p = (async () => {
    // Kept EQUAL to DETECTION_MAX_EDGE (detection-size.ts), deliberately not
    // imported from it. Scanic downsamples anything larger to this before it
    // looks for a contour (its scale-and-grayscale prep), so matching the two means
    // scanic does not resample a frame we already sized. But this bound belongs
    // to scanic: a scanic upgrade that changes its own default should not be
    // silently overridden by ours. If you change one, look at the other.
    const s = new Scanner({ maxProcessingDimension: 800, output: "canvas", ...SCANIC_DETECTION_OPTIONS });
    await s.initialize();
    scanner = s;
  })();

  initPromise = p;
  try {
    await p;
  } catch (e) {
    // Allow a retry: drop the failed promise so the next call starts over.
    if (initPromise === p) initPromise = null;
    throw e;
  }
}

export function getScanner(): Scanner {
  if (!scanner) throw new Error("Scanner not initialized — call initScanner() first");
  return scanner;
}

/**
 * Extract the document from a full-resolution capture.
 *
 * `corners` are in the SAME pixel space as `imageData` — raw-frame pixels.
 *
 * This used to take a `detectionScale` parameter defaulting to 0.4, back when
 * corners arrived in detection space and had to be scaled up here. Callers now
 * convert at the detector boundary using the ratio `detectionSizeFor` actually
 * applied, so by the time corners reach this function there is nothing left to
 * scale. The default was the dangerous part: it silently multiplied
 * already-converted corners by 2.5, and the one caller had to pass `1`
 * explicitly with a comment explaining why. Both are gone.
 *
 * What you see is what gets cropped. With no corners, a quad the warp cannot
 * honour (`isWarpableQuad`), or a failed warp, this hands back the whole frame.
 * It never runs a detection of its own. It used to: with no corners it ran
 * scanic's full-frame detect-and-crop, unpreprocessed and ungated, and filed
 * whatever came back while review showed a different quad. Under scanic 1.6
 * that was usually a speck (0.1% of the frame on corpus frames #1, #12, #46).
 * The caller now passes the inset quad review draws when detection found
 * nothing (ScannerPage.handleCapture).
 */
export async function extractAndEnhance(
  imageData: ImageData,
  corners: Quad | null,
): Promise<{ original: HTMLCanvasElement; enhanced: HTMLCanvasElement }> {
  const fullCanvas = imageDataToCanvas(imageData);

  let outputCanvas: HTMLCanvasElement | null = null;

  if (corners && isWarpableQuad(corners)) {
    try {
      // The exported function, not Scanner#extract: scanic 1.6's typings do not
      // declare the method, and extract needs neither WASM nor an initialized
      // Scanner.
      const result = await extractDocument(fullCanvas, corners, { output: "canvas" });
      const out = result.output as HTMLCanvasElement | null;
      // BOTH dimensions. A near-degenerate quad yields an N x 0 canvas, which
      // passed a width-only check, flowed on as a valid crop, enabled Submit,
      // and became a blank page in the PDF.
      if (result.success && out && out.width > 0 && out.height > 0) {
        outputCanvas = out;
      }
    } catch (e) {
      console.warn("Extract with corners failed:", e);
    }
  }

  const original = outputCanvas ?? fullCanvas;
  return { original, enhanced: enhanceCanvas(original) };
}

function enhanceCanvas(source: HTMLCanvasElement): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext("2d")!;

  // Boost contrast and brightness for document readability
  ctx.filter = "contrast(1.4) brightness(1.15) saturate(0.8)";
  ctx.drawImage(source, 0, 0);
  ctx.filter = "none";

  return out;
}

export function terminateScanner(): void {
  scanner = null;
  initPromise = null;
}
