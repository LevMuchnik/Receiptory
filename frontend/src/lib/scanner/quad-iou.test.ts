import { describe, it, expect } from "vitest";
import { quadIoU, orderQuadByAngle } from "./geometry";
import type { Quad } from "./detector";

/**
 * Regression guards for the scanner's accuracy metric.
 *
 * These exist because `quadIoU` produced the median-IoU numbers that killed the
 * 2026-07-03 DocAligner design (0.099 / 0.062 over 31 labelled frames), and an
 * eng-review outside voice argued those numbers might be a winding artefact
 * rather than a real result. They are not — the spike's high-confidence frames
 * scored 0.91-0.96, which is impossible under inverted winding — but the failure
 * mode is silent (a mis-wound quad reads as IoU 0, indistinguishable from a
 * total miss), so it gets pinned down here rather than argued about again.
 */

const rect = (x0: number, y0: number, x1: number, y1: number): Quad => ({
  topLeft: { x: x0, y: y0 },
  topRight: { x: x1, y: y0 },
  bottomRight: { x: x1, y: y1 },
  bottomLeft: { x: x0, y: y1 },
});

describe("quadIoU — identity", () => {
  it("scores a quad against itself as exactly 1", () => {
    // THE guard. If this ever drifts, every accuracy number in the design docs
    // is measured with a broken ruler.
    const q = rect(10, 20, 110, 320);
    expect(quadIoU(q, q)).toBeCloseTo(1, 10);
  });

  it("scores identity as 1 for a rotated (non-axis-aligned) quad", () => {
    const tilted: Quad = {
      topLeft: { x: 20, y: 5 },
      topRight: { x: 120, y: 30 },
      bottomRight: { x: 100, y: 210 },
      bottomLeft: { x: 5, y: 180 },
    };
    expect(quadIoU(tilted, tilted)).toBeCloseTo(1, 10);
  });

  it("scores identity as 1 for the extreme aspect of a long thermal receipt", () => {
    // ~1:5, the shape the whole detector argument is about.
    const thermal = rect(400, 100, 680, 1500);
    expect(quadIoU(thermal, thermal)).toBeCloseTo(1, 10);
  });
});

describe("quadIoU — known overlaps", () => {
  it("scores two disjoint quads as 0", () => {
    expect(quadIoU(rect(0, 0, 10, 10), rect(100, 100, 110, 110))).toBe(0);
  });

  it("scores a half-overlap correctly", () => {
    // A: x 0..10, B: x 5..15, both y 0..10.
    // intersection 5x10 = 50, union = 100 + 100 - 50 = 150 -> 1/3.
    expect(quadIoU(rect(0, 0, 10, 10), rect(5, 0, 15, 10))).toBeCloseTo(1 / 3, 10);
  });

  it("scores a fully contained quad as the area ratio", () => {
    // inner 50x50 = 2500 inside outer 100x100 = 10000; union is the outer.
    expect(quadIoU(rect(0, 0, 100, 100), rect(25, 25, 75, 75))).toBeCloseTo(0.25, 10);
  });

  it("is symmetric", () => {
    const a = rect(0, 0, 100, 300);
    const b = rect(20, 50, 130, 260);
    expect(quadIoU(a, b)).toBeCloseTo(quadIoU(b, a), 10);
  });

  it("clears the 0.85 hit threshold for a near-perfect detection", () => {
    // A few pixels off on a receipt-sized quad must still count as a hit.
    const truth = rect(100, 100, 400, 1000);
    const detected = rect(103, 104, 397, 996);
    expect(quadIoU(truth, detected)).toBeGreaterThan(0.85);
  });
});

describe("quadIoU — winding sensitivity (the silent failure)", () => {
  const truth = rect(10, 20, 110, 320);

  /** Same four corners, labelled counter-clockwise instead of clockwise. */
  const reversed: Quad = {
    topLeft: truth.topLeft,
    topRight: truth.bottomLeft,
    bottomRight: truth.bottomRight,
    bottomLeft: truth.topRight,
  };

  it("DOCUMENTS the hazard: a counter-wound quad does not score 1 against itself", () => {
    // This is not a bug being fixed, it is a contract being pinned. polygonClip
    // defines "inside" as isLeft >= 0, which assumes TL->TR->BR->BL winding.
    // A detector emitting corners in another convention scores ~0 and looks
    // like a total miss rather than raising an error.
    expect(quadIoU(reversed, reversed)).toBeLessThan(1);
  });

  it("orderQuadByAngle REPAIRS a mis-wound quad back to a perfect self-score", () => {
    // The mitigation: normalise anything of uncertain provenance before
    // measuring with it.
    const repaired = orderQuadByAngle([
      reversed.topLeft, reversed.topRight, reversed.bottomRight, reversed.bottomLeft,
    ]);
    expect(quadIoU(repaired, repaired)).toBeCloseTo(1, 10);
  });

  it("a repaired mis-wound quad scores 1 against the correctly-wound original", () => {
    const repaired = orderQuadByAngle([
      reversed.topLeft, reversed.topRight, reversed.bottomRight, reversed.bottomLeft,
    ]);
    expect(quadIoU(truth, repaired)).toBeCloseTo(1, 10);
  });
});

describe("quadIoU — degenerate inputs", () => {
  it("returns 0 rather than NaN for a zero-area quad", () => {
    const flat = rect(50, 50, 50, 50);
    expect(quadIoU(flat, rect(0, 0, 100, 100))).toBe(0);
    expect(quadIoU(flat, flat)).toBe(0);
  });

  it("returns 0 for a collapsed line quad", () => {
    const line: Quad = {
      topLeft: { x: 0, y: 0 },
      topRight: { x: 100, y: 0 },
      bottomRight: { x: 100, y: 0 },
      bottomLeft: { x: 0, y: 0 },
    };
    expect(quadIoU(line, rect(0, 0, 100, 100))).toBe(0);
  });
});
