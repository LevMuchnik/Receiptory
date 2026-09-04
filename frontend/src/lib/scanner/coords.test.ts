import { describe, it, expect } from "vitest";
import { scaleQuad, clampQuad, normalizeQuad, quadIoU } from "./geometry";
import type { Quad } from "./detector";

/**
 * Coordinate-space round trips.
 *
 * The scanner has four spaces (see the header of `detector.ts`) and this whole
 * subsystem was rewritten because two of them got confused: the overlay mapped
 * detection coordinates to screen with SEPARATE x and y factors over an
 * `object-cover` video, squeezing every quad to 82% width and anchoring it at
 * x=0. Correct at the left edge, worst at the right, and it silently corrupted
 * every on-device judgement made for months.
 *
 * These tests pin the conversions that remain in code. The screen hop is now
 * the browser's job via SVG `preserveAspectRatio`, deliberately untestable here
 * and deliberately not ours to compute.
 */

const quad = (
  tl: [number, number], tr: [number, number], br: [number, number], bl: [number, number],
): Quad => ({
  topLeft: { x: tl[0], y: tl[1] },
  topRight: { x: tr[0], y: tr[1] },
  bottomRight: { x: br[0], y: br[1] },
  bottomLeft: { x: bl[0], y: bl[1] },
});

const corners = (q: Quad) => [q.topLeft, q.topRight, q.bottomRight, q.bottomLeft];

function expectQuadClose(a: Quad, b: Quad, digits = 9) {
  const ca = corners(a);
  const cb = corners(b);
  for (let i = 0; i < 4; i++) {
    expect(ca[i].x).toBeCloseTo(cb[i].x, digits);
    expect(ca[i].y).toBeCloseTo(cb[i].y, digits);
  }
}

describe("detection <-> video round trip", () => {
  // The live loop detects on a downscale of the video frame and reports the
  // ratio it actually used. ScannerPage converts with 1 / detectionScale.
  const DETECTION_SCALE = 0.4;           // 1080x1920 video -> 432x768 detection
  const inDetectionSpace = quad([50, 80], [380, 70], [390, 700], [40, 690]);

  it("is an identity round trip", () => {
    const inVideoSpace = scaleQuad(inDetectionSpace, 1 / DETECTION_SCALE);
    expectQuadClose(scaleQuad(inVideoSpace, DETECTION_SCALE), inDetectionSpace);
  });

  it("lands a detection-space quad in the right place in video space", () => {
    const v = scaleQuad(inDetectionSpace, 1 / DETECTION_SCALE);
    expect(v.topLeft).toEqual({ x: 125, y: 200 });         // 50/0.4, 80/0.4
    expect(v.bottomRight).toEqual({ x: 975, y: 1750 });    // 390/0.4, 700/0.4
  });

  it("scales both axes by ONE ratio — the bug that started all this", () => {
    // An anisotropic mapping (the old scaleX/scaleY) would change the quad's
    // aspect. A single uniform ratio cannot.
    const before = inDetectionSpace;
    const after = scaleQuad(before, 1 / DETECTION_SCALE);
    const widthOf = (q: Quad) => q.topRight.x - q.topLeft.x;
    const heightOf = (q: Quad) => q.bottomLeft.y - q.topLeft.y;
    expect(widthOf(after) / heightOf(after)).toBeCloseTo(widthOf(before) / heightOf(before), 9);
  });

  it("scores IoU 1 against itself after a round trip", () => {
    const there = scaleQuad(inDetectionSpace, 1 / DETECTION_SCALE);
    const back = scaleQuad(there, DETECTION_SCALE);
    expect(quadIoU(inDetectionSpace, back)).toBeCloseTo(1, 9);
  });
});

describe("downscaled-detect <-> raw round trip", () => {
  // The review-entry fresh detect runs on a copy capped at 800px long edge,
  // then multiplies the result by 1 / scale to get back to raw-frame pixels.
  const RAW_W = 1080;
  const RAW_H = 1920;
  const scale = 800 / Math.max(RAW_W, RAW_H); // 0.41666...

  it("is an identity round trip", () => {
    const inSmall = quad([40, 60], [300, 55], [310, 560], [35, 550]);
    const inRaw = scaleQuad(inSmall, 1 / scale);
    expectQuadClose(scaleQuad(inRaw, scale), inSmall);
  });

  it("keeps a full-frame quad inside the raw frame", () => {
    const smallW = Math.round(RAW_W * scale);
    const smallH = Math.round(RAW_H * scale);
    const fullSmall = quad([0, 0], [smallW, 0], [smallW, smallH], [0, smallH]);
    const raw = clampQuad(scaleQuad(fullSmall, 1 / scale), RAW_W, RAW_H);
    // Rounding of the downscaled dimensions can push a corner a hair past the
    // edge; the clamp is what guarantees it lands inside.
    for (const c of corners(raw)) {
      expect(c.x).toBeGreaterThanOrEqual(0);
      expect(c.x).toBeLessThanOrEqual(RAW_W);
      expect(c.y).toBeGreaterThanOrEqual(0);
      expect(c.y).toBeLessThanOrEqual(RAW_H);
    }
  });
});

describe("normalizeQuad — the stored space", () => {
  const RAW_W = 1080;
  const RAW_H = 1920;
  const inRaw = quad([108, 192], [972, 192], [972, 1728], [108, 1728]);

  it("maps a raw-frame quad into 0-1", () => {
    const n = normalizeQuad(inRaw, RAW_W, RAW_H);
    expect(n.topLeft).toEqual({ x: 0.1, y: 0.1 });
    expect(n.bottomRight).toEqual({ x: 0.9, y: 0.9 });
    for (const c of corners(n)) {
      expect(c.x).toBeGreaterThanOrEqual(0);
      expect(c.x).toBeLessThanOrEqual(1);
      expect(c.y).toBeGreaterThanOrEqual(0);
      expect(c.y).toBeLessThanOrEqual(1);
    }
  });

  it("survives the store-then-denormalize trip at a DIFFERENT resolution", () => {
    // This is the whole point of normalizing: the capture frame is 1080x1920
    // but it is stored re-encoded to a 1280px long edge, and the eval later
    // denormalizes against whatever dimensions it actually loads. Pixel corners
    // would describe neither image; normalized ones describe both.
    const stored = normalizeQuad(inRaw, RAW_W, RAW_H);
    const STORED_W = 720;
    const STORED_H = 1280;
    const denorm = quad(
      [stored.topLeft.x * STORED_W, stored.topLeft.y * STORED_H],
      [stored.topRight.x * STORED_W, stored.topRight.y * STORED_H],
      [stored.bottomRight.x * STORED_W, stored.bottomRight.y * STORED_H],
      [stored.bottomLeft.x * STORED_W, stored.bottomLeft.y * STORED_H],
    );
    // Same relative position in the smaller image.
    expect(denorm.topLeft).toEqual({ x: 72, y: 128 });
    expect(quadIoU(denorm, quad([72, 128], [648, 128], [648, 1152], [72, 1152]))).toBeCloseTo(1, 9);
  });

  it("returns the quad untouched for a zero-size frame rather than emitting Infinity", () => {
    expect(normalizeQuad(inRaw, 0, 0)).toEqual(inRaw);
  });
});

describe("clampQuad", () => {
  it("pulls every out-of-frame corner back inside", () => {
    const wild = quad([-50, -20], [2000, -10], [1900, 3000], [-30, 2500]);
    const c = clampQuad(wild, 1080, 1920);
    expect(c.topLeft).toEqual({ x: 0, y: 0 });
    expect(c.topRight).toEqual({ x: 1080, y: 0 });
    expect(c.bottomRight).toEqual({ x: 1080, y: 1920 });
    expect(c.bottomLeft).toEqual({ x: 0, y: 1920 });
  });

  it("leaves a fully in-frame quad untouched", () => {
    const inside = quad([10, 10], [500, 12], [505, 900], [8, 880]);
    expect(clampQuad(inside, 1080, 1920)).toEqual(inside);
  });
});
