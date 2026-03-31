// Scanner engine using Scanic (lightweight document scanner, ~100KB WASM)

import { Scanner } from "scanic";

let scanner: Scanner | null = null;
let initPromise: Promise<void> | null = null;

export async function initScanner(): Promise<void> {
  if (scanner) return;
  if (initPromise) return initPromise;

  initPromise = (async () => {
    scanner = new Scanner({ maxProcessingDimension: 800, output: "canvas" });
    await scanner.initialize();
  })();

  return initPromise;
}

function getScanner(): Scanner {
  if (!scanner) throw new Error("Scanner not initialized — call initScanner() first");
  return scanner;
}

function imageDataToCanvas(imageData: ImageData): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = imageData.width;
  c.height = imageData.height;
  c.getContext("2d")!.putImageData(imageData, 0, 0);
  return c;
}

/** Detect document corners from a (downscaled) video frame. */
export async function detectCorners(imageData: ImageData): Promise<any> {
  const s = getScanner();
  const canvas = imageDataToCanvas(imageData);
  const result = await s.scan(canvas, { mode: "detect" });
  if (result.success && result.corners) {
    return result.corners;
  }
  return null;
}

/**
 * Extract the document from a full-resolution capture.
 * Corners come from detectCorners() which ran on a 0.4x scaled frame,
 * so they need to be scaled up to match the full-res imageData.
 */
export async function extractAndEnhance(
  imageData: ImageData,
  corners: any | null,
  detectionScale: number = 0.4,
): Promise<{ original: HTMLCanvasElement; enhanced: HTMLCanvasElement }> {
  const s = getScanner();
  const fullCanvas = imageDataToCanvas(imageData);

  let outputCanvas: HTMLCanvasElement | null = null;

  // Scale corners from detection resolution to full resolution
  if (corners) {
    const scale = 1 / detectionScale;
    const scaledCorners = {
      topLeft: { x: corners.topLeft.x * scale, y: corners.topLeft.y * scale },
      topRight: { x: corners.topRight.x * scale, y: corners.topRight.y * scale },
      bottomRight: { x: corners.bottomRight.x * scale, y: corners.bottomRight.y * scale },
      bottomLeft: { x: corners.bottomLeft.x * scale, y: corners.bottomLeft.y * scale },
    };

    try {
      const result = await s.extract(fullCanvas, scaledCorners, { output: "canvas" });
      if (result.success && result.output && (result.output as HTMLCanvasElement).width > 0) {
        outputCanvas = result.output as HTMLCanvasElement;
      }
    } catch (e) {
      console.warn("Extract with corners failed:", e);
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
