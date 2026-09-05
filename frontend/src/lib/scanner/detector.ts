/**
 * Detector interface and the `Quad` type every coordinate space is expressed in.
 *
 * COORDINATE SPACES — four of them are in play, and confusing two is the bug
 * this module's documentation exists to prevent. Mirrors the "Coordinate
 * spaces" section of docs/designs/mobile-scanner-detection-and-capture.md.
 *
 *   DETECTION SPACE                    800px longest edge, DETECTION_MAX_EDGE
 *     Detector.detect() operates here
 *     ClassicalParams are tuned here
 *     blur radius = width * 0.125, so cost scales with this
 *     A FIXED edge, never a fraction of the video: a fraction silently
 *     quadrupled this when the camera ladder was raised to 4K.
 *         |
 *         |  / detectionSizeFor().scale   <- converted at the detector boundary
 *         v
 *   VIDEO SPACE                        up to 3840x2160 (either orientation)
 *     onCapture(imageData, corners)    <- corners cross HERE, in video pixels
 *     scanic.extract() consumes this
 *     CaptureReview { raw, corners }
 *         |
 *         |  <svg preserveAspectRatio="xMidYMid slice">   viewfinder
 *         |  <svg preserveAspectRatio="xMidYMid meet">    review
 *         v
 *   SCREEN SPACE                       CSS px
 *     THE BROWSER OWNS THIS.
 *     Never compute cover-scale by hand again. That was defect 1.
 *
 *   STORED SPACE                       1280px longest edge, JPEG q0.85
 *     test-frame-upload.ts downscales on upload
 *     ground_truth_json is normalized 0-1
 *     corners_at_capture_json MUST be normalized 0-1 too
 *     runEval() denormalizes against the LOADED image dims, not stored metadata
 */

export type Pt = { x: number; y: number };
export type Quad = { topLeft: Pt; topRight: Pt; bottomRight: Pt; bottomLeft: Pt };

/**
 * WHY this exists: `corners: null` has more than one cause, and they call for
 * opposite fixes.
 *
 * The viewfinder renders every null the same way ("Position document in
 * frame"), so a document the underlying scanner never found is indistinguishable
 * from one it found and our own geometry gates then threw away. Those are
 * different bugs: the first says the scanner is blind on this scene, the second
 * says a threshold is too tight. Tuning the thresholds cannot fix the first, and
 * making detection faster or sharper cannot fix the second.
 *
 * `error` already separates "the detector broke" from "the detector ran". This
 * splits the second half of that: WHY did a run that worked produce nothing.
 */
export type DetectionOutcome =
  /** A quad was produced and survived every gate. */
  | "accepted"
  /** The underlying scanner ran and found no document. An honest empty frame. */
  | "no-contour"
  /** Corners came back but were unusable (missing, NaN, Infinity). */
  | "malformed-corners"
  | "rejected-area"
  | "rejected-aspect"
  | "rejected-angle"
  | "rejected-convexity"
  /** ML only: below `scoreThreshold`. */
  | "rejected-score"
  /** The detector failed. Always paired with `error`. */
  | "error";

/** The hard-reject outcomes, i.e. "found something, then threw it away". */
export const REJECT_OUTCOMES: readonly DetectionOutcome[] = [
  "rejected-area",
  "rejected-aspect",
  "rejected-angle",
  "rejected-convexity",
  "rejected-score",
];

export interface DetectionResult {
  /** Detected quad in the same space as the ImageData passed to detect(), or null. */
  corners: Quad | null;
  score: number;
  candidates?: { quad: Quad; score: number }[];
  timingMs: number;
  /**
   * Why this result looks the way it does. Optional so a third-party Detector
   * need not implement it; consumers must tolerate `undefined` rather than
   * treating a missing outcome as an error.
   */
  outcome?: DetectionOutcome;
  /**
   * Set when the detector FAILED: it could not init, could not load, threw, or
   * reported that it could not run to a conclusion.
   *
   * `corners: null` WITHOUT `error` means the detector ran fine and found no
   * document ("nothing on the table"). These are different states and the
   * viewfinder renders them differently — "Detector error: {msg}" versus
   * "Position document". Never collapse one into the other.
   */
  error?: string;
}

export interface Detector {
  readonly name: string;
  detect(image: ImageData, params: any): Promise<DetectionResult>;
  getDefaultParams(): any;
}
