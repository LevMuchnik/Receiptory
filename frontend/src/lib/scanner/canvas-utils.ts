/**
 * Canvas <-> ImageData helpers shared by the scanner pipeline.
 *
 * These are the DOM-touching half of the scanner's shared utilities; the pure
 * math lives in `geometry.ts`, which imports nothing from here so it can be
 * unit-tested in plain Node.
 *
 * Not unit-tested on purpose: `ImageData` and `drawImage` do not exist in Node,
 * and these are thin, branchless wrappers around the browser's own scaling.
 */

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
  const longest = Math.max(image.width, image.height);
  if (!(maxEdge > 0) || longest <= maxEdge) return { data: image, scale: 1 };

  // One uniform ratio for both axes. The per-axis ratios (w/image.width,
  // h/image.height) differ from it by at most half a pixel of rounding; using
  // the uniform ratio keeps the mapping isotropic, which is what the aspect
  // preservation promises.
  const scale = maxEdge / longest;
  const w = Math.max(1, Math.round(image.width * scale));
  const h = Math.max(1, Math.round(image.height * scale));

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
