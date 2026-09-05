/**
 * Canvas <-> ImageData helpers shared by the scanner pipeline.
 *
 * These are the DOM-touching half of the scanner's shared utilities; the pure
 * math lives in `geometry.ts`, which imports nothing from here so it can be
 * unit-tested in plain Node.
 *
 * Not unit-tested on purpose: `ImageData` and `drawImage` do not exist in Node,
 * and these are thin, branchless wrappers around the browser's own scaling.
 * Anything here with real arithmetic in it belongs in a DOM-free module that
 * CAN be tested — `downscaleImageData` delegates its sizing to
 * `detection-size.ts` for exactly that reason.
 */

import { detectionSizeFor } from "./detection-size";

/** Wrap an ImageData in a canvas of the same dimensions. */
export function imageDataToCanvas(image: ImageData): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = image.width;
  c.height = image.height;
  c.getContext("2d")!.putImageData(image, 0, 0);
  return c;
}

/** Read a canvas back out as ImageData. */
export function canvasToImageData(canvas: HTMLCanvasElement): ImageData {
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

/**
 * Downscale so the LONGEST edge is at most `maxEdge`, preserving aspect ratio.
 *
 * Returns the scale factor actually applied (newSize / originalSize) so callers
 * can map coordinates detected in the downscaled image back to the original
 * space. Callers must NOT recompute this ratio themselves — deriving it twice
 * is how coordinate-space bugs are born.
 *
 * If the image already fits within `maxEdge` it is returned unchanged with
 * `scale: 1` (no copy, no re-encode).
 */
export function downscaleImageData(
  image: ImageData,
  maxEdge: number,
): { data: ImageData; scale: number } {
  // The size decision — including the no-upscale clamp that gives `scale === 1`
  // its meaning — lives in `detection-size.ts` and is unit-tested there. This
  // function owns only the canvas work. Keeping the arithmetic in one place is
  // the point: the live viewfinder loop sizes its frame from the same helper,
  // and two copies of "how big should the detection frame be" is exactly the
  // duplication that let the 4K raise silently quadruple it.
  const { w, h, scale } = detectionSizeFor(image.width, image.height, maxEdge);
  if (scale === 1) return { data: image, scale: 1 };

  const src = imageDataToCanvas(image);
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(src, 0, 0, w, h);

  return { data: ctx.getImageData(0, 0, w, h), scale };
}

/**
 * Rotate a canvas by a multiple of 90 degrees. Returns the SAME canvas for 0,
 * so the common case allocates nothing.
 *
 * Hoisted here because two copies existed: pdf-builder's `applyRotation` and
 * CaptureReview's `rotateCanvas`.
 */
export function rotateCanvas(canvas: HTMLCanvasElement, degrees: number): HTMLCanvasElement {
  const deg = ((degrees % 360) + 360) % 360;
  if (deg === 0) return canvas;

  const out = document.createElement("canvas");
  const ctx = out.getContext("2d")!;
  if (deg === 90 || deg === 270) {
    out.width = canvas.height;
    out.height = canvas.width;
  } else {
    out.width = canvas.width;
    out.height = canvas.height;
  }
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate((deg * Math.PI) / 180);
  ctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
  return out;
}

/**
 * Encode a canvas to a JPEG blob.
 *
 * Preferred over `toDataURL` wherever the result is uploaded or held: a blob is
 * binary, while a data URL is a base64 string roughly 4/3 the size that lives on
 * the JS heap until it is dropped.
 */
export function canvasToJpegBlob(canvas: HTMLCanvasElement, quality = 0.92): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}
