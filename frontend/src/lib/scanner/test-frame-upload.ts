import { api } from "@/lib/api";
import type { Quad } from "./detector";
import { normalizeQuad } from "./geometry";

const LONG_EDGE = 1280;
const JPEG_QUALITY = 0.85;

/**
 * Fire-and-forget upload of a capture frame to the scanner test corpus.
 *
 * `corners` MUST be in the pixel space of `imageData` (raw-frame pixels). They
 * are normalized to 0-1 before storage, because the frame itself is re-encoded
 * down to a 1280px long edge on the way out: pixel corners would describe
 * neither the source frame nor the stored JPEG and are useless for evaluation.
 * Normalized 0-1 is also the space `ground_truth_json` uses
 * (both now share `geometry.normalizeQuad`), so capture corners and ground truth
 * finally share one space and `runEval` can compare them directly.
 */
export function uploadTestFrame(
  imageData: ImageData,
  detectorName: string | null,
  corners: Quad | null,
): void {
  const normalized =
    corners && imageData.width > 0 && imageData.height > 0
      ? normalizeQuad(corners, imageData.width, imageData.height)
      : null;

  // Fire-and-forget. Don't block the scanner flow on upload latency or auth errors.
  encodeJpeg(imageData)
    .then((blob) => {
      if (!blob) return;
      return api.uploadScannerTestFrame(blob, {
        width: imageData.width,
        height: imageData.height,
        detector_name: detectorName ?? undefined,
        corners_at_capture_json: normalized ? JSON.stringify(normalized) : undefined,
      });
    })
    .catch((err) => {
      console.warn("Test-frame upload failed:", err);
    });
}

async function encodeJpeg(image: ImageData): Promise<Blob | null> {
  const longEdge = Math.max(image.width, image.height);
  const scale = longEdge > LONG_EDGE ? LONG_EDGE / longEdge : 1;
  const w = Math.round(image.width * scale);
  const h = Math.round(image.height * scale);

  // createImageBitmap takes ImageData directly, so the full-resolution canvas
  // that used to exist purely to downscale FROM is gone. At 4K that allocation
  // was ~33MB on the capture path, for a frame we immediately shrink to 1280.
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d")!;
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(image);
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close();
  } else {
    // Fallback for anything without createImageBitmap: the old two-canvas path.
    const src = document.createElement("canvas");
    src.width = image.width;
    src.height = image.height;
    src.getContext("2d")!.putImageData(image, 0, 0);
    ctx.drawImage(src, 0, 0, w, h);
  }

  return new Promise((resolve) => {
    out.toBlob((b) => resolve(b), "image/jpeg", JPEG_QUALITY);
  });
}
