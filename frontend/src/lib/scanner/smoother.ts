/**
 * Temporal smoothing for the live detection quad.
 *
 * DOM-free by contract: no `document`, no `ImageData`, no `window`. The only
 * ambient global is `performance.now()`, used solely as the DEFAULT value of an
 * injectable timestamp parameter, so the vitest suite can drive this class with
 * a fake clock in plain Node — no jsdom.
 *
 * RELEASE RULES (this is the fix for the ghost-lock defect). The old smoother
 * early-returned on a null detection without clearing `ema`, so the last quad
 * persisted forever while the drift-reject gate refused every new quad more
 * than `driftRejectFraction` of the frame diagonal away from that stale ghost.
 * Two rules now let go, in this precedence:
 *
 *   1. `maxConsecutiveDriftRejects` (2) consecutive drift-rejects -> reset().
 *      PRIMARY. This is what fires in the ghost-lock case, where `push` is
 *      still being called with perfectly valid quads that the gate rejects.
 *
 *   2. `staleMs` (250ms) with no ACCEPTED detection -> reset().
 *      Backstop. Time-based, deliberately not a frame count: the detection
 *      cadence is now an ~80ms floor inside `requestVideoFrameCallback`, not
 *      the old `rAF % 5`, so any frame count would encode the wrong duration.
 *
 * Rule 2 is evaluated in `getEMA()` as well as `push()`, because the failure
 * that matters most -- the detector THROWING -- never calls `push` at all.
 * Keeping the expiry read-side means the smoother releases on its own clock
 * even if every caller stops feeding it.
 */

import type { Quad, DetectionResult } from "./detector";

interface Sample {
  quad: Quad;
  timestamp: number;
}

export interface SmootherOptions {
  bufferSize?: number;
  alpha?: number;
  driftRejectFraction?: number;
  warmupFrames?: number;
  /** Consecutive drift-rejects that force a reset. Primary release rule. */
  maxConsecutiveDriftRejects?: number;
  /** Milliseconds without an accepted detection before the lock expires. */
  staleMs?: number;
}

const DEFAULT_OPTS: Required<SmootherOptions> = {
  bufferSize: 10,
  alpha: 0.4,
  driftRejectFraction: 0.15,
  warmupFrames: 3,
  maxConsecutiveDriftRejects: 2,
  staleMs: 250,
};

export class TemporalSmoother {
  private buffer: Sample[] = [];
  private ema: Quad | null = null;
  private opts: Required<SmootherOptions>;
  private consecutiveDriftRejects = 0;
  private lastAcceptedMs: number | null = null;

  constructor(options: SmootherOptions = {}) {
    this.opts = { ...DEFAULT_OPTS, ...options };
  }

  reset(): void {
    this.buffer = [];
    this.ema = null;
    this.consecutiveDriftRejects = 0;
    this.lastAcceptedMs = null;
  }

  push(detection: DetectionResult, frameDiagonal: number, timestamp: number = performance.now()): void {
    this.expire(timestamp);

    if (!detection.corners) return;
    const quad = detection.corners;

    if (this.buffer.length >= this.opts.warmupFrames && this.ema) {
      const drift = maxCornerDrift(quad, this.ema);
      if (drift > frameDiagonal * this.opts.driftRejectFraction) {
        this.consecutiveDriftRejects++;
        if (this.consecutiveDriftRejects >= this.opts.maxConsecutiveDriftRejects) {
          // Release rule 1. The next push starts a fresh lock, because an empty
          // buffer skips the drift gate for `warmupFrames` frames.
          this.reset();
        }
        return;
      }
    }

    this.consecutiveDriftRejects = 0;
    this.lastAcceptedMs = timestamp;

    this.buffer.push({ quad, timestamp });
    if (this.buffer.length > this.opts.bufferSize) this.buffer.shift();

    this.ema = this.ema ? blendQuads(this.ema, quad, this.opts.alpha) : quad;
  }

  /**
   * Current smoothed quad, or null.
   *
   * Evaluates the staleness expiry against `timestamp` BEFORE answering, so a
   * caller that stops pushing (detector throwing, camera stalled) still sees
   * the lock released. Do not "optimize" this into a plain getter.
   */
  getEMA(timestamp: number = performance.now()): Quad | null {
    this.expire(timestamp);
    return this.ema;
  }

  /** Release rule 2: drop the lock once nothing has been accepted for `staleMs`. */
  private expire(timestamp: number): void {
    if (this.ema === null || this.lastAcceptedMs === null) return;
    if (timestamp - this.lastAcceptedMs >= this.opts.staleMs) this.reset();
  }
}

function maxCornerDrift(a: Quad, b: Quad): number {
  return Math.max(
    dist(a.topLeft, b.topLeft),
    dist(a.topRight, b.topRight),
    dist(a.bottomRight, b.bottomRight),
    dist(a.bottomLeft, b.bottomLeft),
  );
}

function dist(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function blendQuads(prev: Quad, cur: Quad, alpha: number): Quad {
  return {
    topLeft: blendPt(prev.topLeft, cur.topLeft, alpha),
    topRight: blendPt(prev.topRight, cur.topRight, alpha),
    bottomRight: blendPt(prev.bottomRight, cur.bottomRight, alpha),
    bottomLeft: blendPt(prev.bottomLeft, cur.bottomLeft, alpha),
  };
}

function blendPt(prev: { x: number; y: number }, cur: { x: number; y: number }, alpha: number) {
  return {
    x: (1 - alpha) * prev.x + alpha * cur.x,
    y: (1 - alpha) * prev.y + alpha * cur.y,
  };
}
