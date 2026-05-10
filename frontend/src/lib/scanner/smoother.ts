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
  autoCaptureStability?: number;
  autoCaptureSustainMs?: number;
}

const DEFAULT_OPTS: Required<SmootherOptions> = {
  bufferSize: 10,
  alpha: 0.4,
  driftRejectFraction: 0.15,
  warmupFrames: 3,
  autoCaptureStability: 0.9,
  autoCaptureSustainMs: 700,
};

export class TemporalSmoother {
  private buffer: Sample[] = [];
  private ema: Quad | null = null;
  private opts: Required<SmootherOptions>;
  private stableSinceMs: number | null = null;

  constructor(options: SmootherOptions = {}) {
    this.opts = { ...DEFAULT_OPTS, ...options };
  }

  reset(): void {
    this.buffer = [];
    this.ema = null;
    this.stableSinceMs = null;
  }

  push(detection: DetectionResult, frameDiagonal: number, timestamp: number = performance.now()): void {
    if (!detection.corners) {
      this.stableSinceMs = null;
      return;
    }
    const quad = detection.corners;

    if (this.buffer.length >= this.opts.warmupFrames && this.ema) {
      const drift = maxCornerDrift(quad, this.ema);
      if (drift > frameDiagonal * this.opts.driftRejectFraction) {
        this.stableSinceMs = null;
        return;
      }
    }

    this.buffer.push({ quad, timestamp });
    if (this.buffer.length > this.opts.bufferSize) this.buffer.shift();

    this.ema = this.ema ? blendQuads(this.ema, quad, this.opts.alpha) : quad;

    const stab = this.computeStability(frameDiagonal);
    if (stab >= this.opts.autoCaptureStability) {
      if (this.stableSinceMs === null) this.stableSinceMs = timestamp;
    } else {
      this.stableSinceMs = null;
    }
  }

  getEMA(): Quad | null {
    return this.ema;
  }

  getStability(frameDiagonal: number): number {
    return this.computeStability(frameDiagonal);
  }

  shouldAutoCapture(timestamp: number = performance.now()): boolean {
    if (this.stableSinceMs === null) return false;
    return timestamp - this.stableSinceMs >= this.opts.autoCaptureSustainMs;
  }

  private computeStability(frameDiagonal: number): number {
    if (this.buffer.length < 2 || !this.ema) return 0;
    let maxDrift = 0;
    for (const s of this.buffer) {
      const d = maxCornerDrift(s.quad, this.ema);
      if (d > maxDrift) maxDrift = d;
    }
    const norm = maxDrift / Math.max(frameDiagonal * this.opts.driftRejectFraction, 1);
    return Math.max(0, 1 - norm);
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
