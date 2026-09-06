// Scanner engine using Scanic (lightweight document scanner, ~100KB WASM)

import { Scanner } from "scanic";

import { imageDataToCanvas } from "./scanner/canvas-utils";

let scanner: Scanner | null = null;
let initPromise: Promise<void> | null = null;

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
 */
export async function initScanner(): Promise<void> {
  if (scanner) return;
  if (initPromise) return initPromise;

  const p = (async () => {
    // Kept EQUAL to DETECTION_MAX_EDGE (detection-size.ts), deliberately not
    // imported from it. Scanic downsamples anything larger to this before it
    // looks for a contour (prepareScaleAndGrayscale), so matching the two means
    // scanic does not resample a frame we already sized. But this bound belongs
    // to scanic: a scanic upgrade that changes its own default should not be
    // silently overridden by ours. If you change one, look at the other.
    const s = new Scanner({ maxProcessingDimension: 800, output: "canvas" });
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
 */
export async function extractAndEnhance(
  imageData: ImageData,
  corners: any | null,
): Promise<{ original: HTMLCanvasElement; enhanced: HTMLCanvasElement }> {
  const s = getScanner();
  const fullCanvas = imageDataToCanvas(imageData);

  let outputCanvas: HTMLCanvasElement | null = null;

  if (corners) {
    try {
      const result = await s.extract(fullCanvas, corners, { output: "canvas" });
      const out = result.output as HTMLCanvasElement | undefined;
      // BOTH dimensions. A near-degenerate quad yields an N x 0 canvas, which
      // passed a width-only check, flowed on as a valid crop, enabled Submit,
      // and became a blank page in the PDF.
      if (result.success && out && out.width > 0 && out.height > 0) {
        outputCanvas = out;
      }
    } catch (e) {
      console.warn("Extract with corners failed:", e);
    }

    // Deliberately NOT falling through to the full-frame detect-and-crop below.
    // That rung runs a FRESH detection and crops to whatever scanic picks,
    // ignoring the corners the user just dragged -- so a failed warp would file
    // a crop they never chose while the review screen kept showing their quad.
    // If their corners could not be honoured, hand back the whole frame instead.
    if (!outputCanvas) {
      const enhanced = enhanceCanvas(fullCanvas);
      return { original: fullCanvas, enhanced };
    }
  }

  // Fallback: run full detect+extract on the full-res frame
  if (!outputCanvas) {
    try {
      const result = await s.scan(fullCanvas, { mode: "extract", output: "canvas" });
      if (result.success && result.output && (result.output as HTMLCanvasElement).width > 0) {
        outputCanvas = result.output as HTMLCanvasElement;
      }
    } catch (e) {
      console.warn("Full scan failed:", e);
    }
  }

  // Final fallback: raw frame
  if (!outputCanvas) {
    outputCanvas = fullCanvas;
  }

  const enhanced = enhanceCanvas(outputCanvas);
  return { original: outputCanvas, enhanced };
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
