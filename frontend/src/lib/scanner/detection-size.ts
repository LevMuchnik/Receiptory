/**
 * The one resolution the classical detector is tuned for.
 *
 * `detector.ts` documents DETECTION SPACE as "~800px longest edge", and every
 * `ClassicalParams` default was tuned there. Three places have to agree on it:
 *
 *   - the live viewfinder loop      (CameraViewfinder)
 *   - the review-entry re-detect    (ScannerPage)
 *   - the Lab's detect / eval       (ScannerLabPage)
 *
 * Scanic's own `maxProcessingDimension` (opencv-loader.ts) is kept EQUAL to this
 * number but is deliberately NOT derived from it: that bound belongs to scanic,
 * and a scanic upgrade that changes its default should not be silently
 * overridden by ours.
 *
 * Why 800 specifically, and why a fixed edge rather than a fraction of the
 * video: scanic downsamples anything larger to 800 before it looks for a
 * contour (`prepareScaleAndGrayscale`, scanic.js:961), so pixels above this are
 * discarded — but only AFTER `classical-detector.preprocess()` has paid five
 * per-pixel JS passes and two canvas round-trips on them. A fraction of the
 * video silently rescales that cost with whatever the camera ladder negotiated;
 * a fixed edge does not.
 */
export const DETECTION_MAX_EDGE = 800;

export interface DetectionSize {
  w: number;
  h: number;
  /**
   * newSize / originalSize — ONE uniform ratio for both axes, exactly 1 when
   * the source was left alone. Callers map coordinates back with this and must
   * not recompute it from `w / srcW`: the two differ by up to half a pixel of
   * rounding, and deriving the same ratio twice is how coordinate-space bugs
   * are born (see `detector.ts`'s COORDINATE SPACES header).
   */
  scale: number;
}

/**
 * Size a detection frame from a source frame, capped at `maxEdge`.
 *
 * NEVER UPSCALES. This is the property that makes a fixed edge safe where the
 * old `videoWidth * 0.4` was safe for free: a fraction below 1 is monotonically
 * a downscale, but a fixed 800px target is not. A 640x480 webcam, or the bottom
 * rung of the camera ladder, would otherwise be blown up to 800px — more pixels
 * than the source, every one of them invented by `drawImage`, at strictly more
 * cost than doing nothing. `scale` stays exactly 1 there so callers can skip the
 * copy entirely.
 *
 * Non-positive or non-finite inputs fall through the same guard and are returned
 * unchanged with `scale: 1`, rather than being turned into a plausible-looking
 * size. `video.videoWidth` is 0 between camera-ready and the first decoded
 * frame, and callers already guard it; this only ensures nothing downstream
 * receives a fabricated dimension.
 */
export function detectionSizeFor(
  srcW: number,
  srcH: number,
  maxEdge: number = DETECTION_MAX_EDGE,
): DetectionSize {
  const longest = Math.max(srcW, srcH);
  // `NaN > 0` is false, so this one guard covers zero, negative and NaN.
  if (!(maxEdge > 0) || !(longest > 0) || longest <= maxEdge) {
    return { w: srcW, h: srcH, scale: 1 };
  }
  const scale = maxEdge / longest;
  return {
    w: Math.max(1, Math.round(srcW * scale)),
    h: Math.max(1, Math.round(srcH * scale)),
    scale,
  };
}
