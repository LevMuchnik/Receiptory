import { describe, it, expect } from "vitest";
import { TemporalSmoother } from "./smoother";
import type { DetectionResult, Quad } from "./detector";

/**
 * The ghost-lock regression suite.
 *
 * The old smoother early-returned on a null detection without clearing its EMA,
 * so the last quad persisted forever, the "Document detected" badge stayed true
 * forever, and the drift gate then refused every new quad more than 15% of the
 * frame diagonal away from that stale ghost. Losing the receipt once could lock
 * the box onto nothing, permanently, until the page was reloaded.
 *
 * Every test here drives an injected clock. No DOM, no fake timers, no jsdom.
 */

const DIAG = 1000;            // frame diagonal -> drift threshold = 150 at 0.15
const OVER_THRESHOLD = 400;   // comfortably past the gate
const WITHIN_THRESHOLD = 10;  // comfortably inside it

function quadAt(x: number, y: number): Quad {
  return {
    topLeft: { x, y },
    topRight: { x: x + 100, y },
    bottomRight: { x: x + 100, y: y + 200 },
    bottomLeft: { x, y: y + 200 },
  };
}

function found(quad: Quad): DetectionResult {
  return { corners: quad, score: 1, timingMs: 5 };
}

const nothingFound: DetectionResult = { corners: null, score: 0, timingMs: 5 };
const detectorFailed: DetectionResult = { corners: null, score: 0, timingMs: 0, error: "Scanner error: boom" };

/**
 * Push four accepted detections at the same place to get past `warmupFrames`.
 * Returns the timestamp of the LAST accepted push, so callers can reason about
 * staleness as an offset from a real acceptance rather than from an empty slot.
 */
function warmUp(s: TemporalSmoother, quad: Quad, startMs = 0, stepMs = 80): number {
  let t = startMs;
  for (let i = 0; i < 4; i++) {
    s.push(found(quad), DIAG, t);
    if (i < 3) t += stepMs;
  }
  return t;
}

describe("first lock", () => {
  it("adopts the first detection verbatim rather than blending from nothing", () => {
    const s = new TemporalSmoother();
    const q = quadAt(10, 20);
    s.push(found(q), DIAG, 0);
    expect(s.getEMA(0)).toEqual(q);
  });

  it("returns null before anything has been pushed", () => {
    expect(new TemporalSmoother().getEMA(0)).toBeNull();
  });

  it("blends subsequent detections toward the new quad", () => {
    const s = new TemporalSmoother({ alpha: 0.5 });
    s.push(found(quadAt(0, 0)), DIAG, 0);
    s.push(found(quadAt(10, 0)), DIAG, 10);
    // alpha 0.5 -> halfway between 0 and 10.
    expect(s.getEMA(10)!.topLeft.x).toBeCloseTo(5, 10);
  });
});

describe("warmup", () => {
  it("skips the drift gate while the buffer is below warmupFrames", () => {
    const s = new TemporalSmoother();
    s.push(found(quadAt(0, 0)), DIAG, 0);
    // Second push is a huge jump but warmup has not completed, so it is accepted.
    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, 10);
    expect(s.getEMA(10)!.topLeft.x).toBeGreaterThan(0);
  });
});

describe("release rule 1 — consecutive drift rejects", () => {
  it("rejects a single far quad and keeps the existing lock", () => {
    const s = new TemporalSmoother();
    const t = warmUp(s, quadAt(0, 0));
    const before = s.getEMA(t);

    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t);
    expect(s.getEMA(t)).toEqual(before); // one reject is not enough to let go
  });

  it("RELEASES after two consecutive drift rejects — the ghost-lock fix", () => {
    const s = new TemporalSmoother();
    const t = warmUp(s, quadAt(0, 0));

    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t);
    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t + 80);

    expect(s.getEMA(t + 80)).toBeNull();
  });

  it("re-locks onto the new position immediately after releasing", () => {
    // The behaviour that matters in the hand: re-frame the receipt and the box
    // must follow, not stay fenced out by the gate for the rest of the session.
    const s = new TemporalSmoother();
    let t = warmUp(s, quadAt(0, 0));

    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t); t += 80;
    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t); t += 80;
    expect(s.getEMA(t)).toBeNull();

    const moved = quadAt(OVER_THRESHOLD, 0);
    s.push(found(moved), DIAG, t);
    expect(s.getEMA(t)).toEqual(moved);
  });

  it("resets the reject streak when a good detection lands in between", () => {
    const s = new TemporalSmoother();
    let t = warmUp(s, quadAt(0, 0));

    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t); t += 80;   // reject 1
    s.push(found(quadAt(WITHIN_THRESHOLD, 0)), DIAG, t); t += 80; // accepted, streak cleared
    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t);            // reject 1 again, not 2

    expect(s.getEMA(t)).not.toBeNull();
  });
});

describe("release rule 2 — staleness", () => {
  it("releases after 250ms with no accepted detection", () => {
    const s = new TemporalSmoother();
    const t = warmUp(s, quadAt(0, 0));
    expect(s.getEMA(t)).not.toBeNull();
    expect(s.getEMA(t + 250)).toBeNull();
  });

  it("holds the lock just under the threshold", () => {
    const s = new TemporalSmoother();
    const t = warmUp(s, quadAt(0, 0));
    expect(s.getEMA(t + 249)).not.toBeNull();
  });

  it("EXPIRES VIA getEMA ALONE when the detector throws and push is never called", () => {
    // This is why the expiry lives on the read side. When detect() rejects, the
    // viewfinder's catch path runs and push() is never reached — an expiry that
    // only ran inside push() would leave the ghost on screen indefinitely.
    const s = new TemporalSmoother();
    const t = warmUp(s, quadAt(0, 0));

    // No further push of any kind. Only the clock advances.
    expect(s.getEMA(t + 100)).not.toBeNull();
    expect(s.getEMA(t + 300)).toBeNull();
  });

  it("expires while the camera keeps reporting an empty frame", () => {
    const s = new TemporalSmoother();
    let t = warmUp(s, quadAt(0, 0));
    for (let i = 0; i < 5; i++) {
      s.push(nothingFound, DIAG, t);
      t += 80;
    }
    expect(s.getEMA(t)).toBeNull();
  });

  it("expires while the detector keeps reporting an error", () => {
    const s = new TemporalSmoother();
    let t = warmUp(s, quadAt(0, 0));
    for (let i = 0; i < 5; i++) {
      s.push(detectorFailed, DIAG, t);
      t += 80;
    }
    expect(s.getEMA(t)).toBeNull();
  });

  it("keeps the lock alive while detections keep arriving", () => {
    const s = new TemporalSmoother();
    let t = warmUp(s, quadAt(0, 0));
    for (let i = 0; i < 20; i++) {
      s.push(found(quadAt(WITHIN_THRESHOLD, 0)), DIAG, t);
      t += 80; // under staleMs, so the lock never expires
    }
    expect(s.getEMA(t)).not.toBeNull();
  });

  it("honours a custom staleMs", () => {
    const s = new TemporalSmoother({ staleMs: 1000 });
    const t = warmUp(s, quadAt(0, 0));
    expect(s.getEMA(t + 400)).not.toBeNull();
    expect(s.getEMA(t + 1000)).toBeNull();
  });
});

describe("reset", () => {
  it("clears the lock and lets the next detection start fresh", () => {
    const s = new TemporalSmoother();
    const t = warmUp(s, quadAt(0, 0));
    s.reset();
    expect(s.getEMA(t)).toBeNull();

    const q = quadAt(500, 500);
    s.push(found(q), DIAG, t);
    expect(s.getEMA(t)).toEqual(q); // adopted verbatim, not blended with the old ghost
  });

  it("clears the drift-reject streak", () => {
    const s = new TemporalSmoother();
    let t = warmUp(s, quadAt(0, 0));
    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t); t += 80; // reject 1
    s.reset();

    // Streak cleared, so a fresh lock followed by one reject must survive.
    t = warmUp(s, quadAt(0, 0), t);
    s.push(found(quadAt(OVER_THRESHOLD, 0)), DIAG, t);
    expect(s.getEMA(t)).not.toBeNull();
  });
});
