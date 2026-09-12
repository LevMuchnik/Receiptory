import { describe, it, expect } from "vitest";
import {
  polygonArea,
  convexHullArea,
  dist,
  lerp,
  interiorAngles,
  orderQuadByAngle,
  quadAreaFraction,
  clampPtToFrame,
  quadsEqual,
  scaleDetectionResult,
  insetQuad,
  quadFromPlacedPoints,
  advancePlacement,
  MIN_TAP_SEPARATION_FRACTION,
  isWarpableQuad,
  MIN_WARP_SIDE_PX,
} from "./geometry";
import type { DetectionResult, Pt, Quad } from "./detector";

const quadPts = (q: Quad): Pt[] => [q.topLeft, q.topRight, q.bottomRight, q.bottomLeft];

const Q = (tl: [number, number], tr: [number, number], br: [number, number], bl: [number, number]): Quad => ({
  topLeft: { x: tl[0], y: tl[1] },
  topRight: { x: tr[0], y: tr[1] },
  bottomRight: { x: br[0], y: br[1] },
  bottomLeft: { x: bl[0], y: bl[1] },
});

describe("isWarpableQuad — no black pages from a folded crop", () => {
  // The three degenerate shapes below each made scanic 1.6's extract return a
  // 100% black page with success:true (reproduced on a 2160x3840 frame).
  it("rejects three corners on one frame edge", () => {
    expect(isWarpableQuad(Q([0, 0], [900, 1500], [0, 2500], [0, 3839]))).toBe(false);
  });

  it("rejects four collinear corners", () => {
    expect(isWarpableQuad(Q([0, 0], [700, 0], [1400, 0], [2159, 0]))).toBe(false);
  });

  it("rejects two coincident corners", () => {
    expect(isWarpableQuad(Q([0, 0], [2159, 0], [2159, 0], [0, 3839]))).toBe(false);
  });

  it("rejects a bow-tie", () => {
    expect(isWarpableQuad(Q([0, 0], [1000, 1000], [1000, 0], [0, 1000]))).toBe(false);
  });

  it("rejects a side shorter than MIN_WARP_SIDE_PX", () => {
    expect(isWarpableQuad(Q([0, 0], [2000, 0], [2000, MIN_WARP_SIDE_PX - 1], [0, MIN_WARP_SIDE_PX - 1]))).toBe(false);
  });

  it("rejects non-finite corners", () => {
    expect(isWarpableQuad(Q([0, 0], [100, 0], [100, NaN], [0, 100]))).toBe(false);
    expect(isWarpableQuad(Q([0, 0], [Infinity, 0], [100, 100], [0, 100]))).toBe(false);
  });

  it("accepts real crops: the inset default, a tilted receipt, and either winding", () => {
    expect(isWarpableQuad(insetQuad(2160, 3840))).toBe(true);
    // The Naya capture's quad in raw-frame pixels: long, thin, sheared.
    expect(isWarpableQuad(Q([725, 792], [1205, 792], [1257, 2923], [773, 2923]))).toBe(true);
    // Counter-clockwise winding is still convex.
    expect(isWarpableQuad(Q([0, 0], [0, 1000], [800, 1000], [800, 0]))).toBe(true);
  });

  it("accepts the full-frame quad that 'Use full frame' sets", () => {
    expect(isWarpableQuad(Q([0, 0], [2160, 0], [2160, 3840], [0, 3840]))).toBe(true);
  });
});

describe("isWarpableQuad — where the rejection boundary actually sits", () => {
  /** A quad whose TOP-RIGHT corner turns by `d` pixels over a 1000px run. */
  const flatByPixels = (d: number): Quad => Q([0, 0], [1000, 0], [2000, d], [1000, 2000]);

  it("rejects a corner flatter than the turn threshold and accepts one just past it", () => {
    // The guard is |sin(turn)| >= 0.01, so over a 1000px run the corner has to
    // rise ~10px. An exactly-collinear corner is only the extreme of this: the
    // singular solve that black-pages a scan starts well before three corners
    // line up perfectly, which is why the test that matters is the near miss.
    expect(isWarpableQuad(flatByPixels(0))).toBe(false);
    expect(isWarpableQuad(flatByPixels(9))).toBe(false);
    expect(isWarpableQuad(flatByPixels(11))).toBe(true);
  });

  it("judges turn by angle, not by pixels — the same shape at any size gets the same verdict", () => {
    // `cross` and `ab * bc` both scale with the square of the shape, so the
    // ratio does not. A 4K capture and a review-space preview of the same quad
    // must not disagree about whether it can be warped.
    const scaled = (q: Quad, s: number): Quad =>
      Q(
        [q.topLeft.x * s, q.topLeft.y * s],
        [q.topRight.x * s, q.topRight.y * s],
        [q.bottomRight.x * s, q.bottomRight.y * s],
        [q.bottomLeft.x * s, q.bottomLeft.y * s],
      );
    // Scales chosen to keep every side above MIN_WARP_SIDE_PX, so only the turn
    // test is in play.
    for (const s of [0.02, 1, 100]) {
      expect(isWarpableQuad(scaled(flatByPixels(3), s))).toBe(false);
      expect(isWarpableQuad(scaled(flatByPixels(300), s))).toBe(true);
    }
  });

  it("ACCEPTS a concave quad — one drag handle pulled inside the other three", () => {
    // NOT a bow-tie: angle-ordered, simple, and exactly what `onCornersCommit`
    // hands over when someone drags a corner past the opposite diagonal. The
    // warp honours it (oddly, but deterministically) and review is drawing that
    // same dented box, so refusing it would file the whole frame instead and
    // re-open the shown-vs-filed split this branch closed. Only shapes the warp
    // cannot honour — flat corners, tiny sides, bow-ties — are refused.
    const concave = orderQuadByAngle([
      { x: 0, y: 0 },
      { x: 1000, y: 0 },
      { x: 500, y: 400 },
      { x: 0, y: 1000 },
    ]);
    expect(isSelfIntersecting(concave)).toBe(false);
    expect(isWarpableQuad(concave)).toBe(true);
  });

  it("still rejects a bow-tie once convexity is no longer the test", () => {
    // The bow-tie and the concave quad both have mixed turn directions, so the
    // edge-crossing check is the only thing left telling them apart.
    const bowtie = Q([0, 0], [1000, 1000], [1000, 0], [0, 1000]);
    expect(isSelfIntersecting(bowtie)).toBe(true);
    expect(isWarpableQuad(bowtie)).toBe(false);
  });

  it("honours a caller-supplied minSide instead of the default", () => {
    const thin = Q([0, 0], [2000, 0], [2000, 20], [0, 20]);
    expect(isWarpableQuad(thin)).toBe(true);
    expect(isWarpableQuad(thin, 8)).toBe(true);
    expect(isWarpableQuad(thin, 21)).toBe(false);
  });

  it("rejects a quad with a missing corner rather than throwing on it", () => {
    // Reachable from anything that builds a Quad out of an array — a short
    // corner list reads `undefined` off the end. A throw here would surface as
    // "Capture failed" on a scan that could simply have fallen back.
    const holed = { ...Q([0, 0], [1000, 0], [1000, 1000], [0, 1000]), bottomLeft: undefined };
    expect(isWarpableQuad(holed as unknown as Quad)).toBe(false);
  });
});

describe("isWarpableQuad ∘ insetQuad — the no-detection capture still crops", () => {
  // ScannerPage.handleCapture now hands `insetQuad(w, h)` to extractAndEnhance
  // when detection found nothing, and extractAndEnhance drops any quad
  // isWarpableQuad refuses. If these two ever disagreed, EVERY no-detection
  // capture would silently file the uncropped frame while review drew an inset
  // box — the exact "review shows one thing, the PDF holds another" defect this
  // branch set out to remove.
  it("accepts the inset quad at every capture size the camera ladder produces", () => {
    const sizes: [number, number][] = [
      [640, 480],
      [1280, 720],
      [1920, 1080],
      [1080, 1920],
      [2160, 3840],
      [3840, 2160],
      [4032, 3024],
    ];
    for (const [w, h] of sizes) {
      expect(isWarpableQuad(insetQuad(w, h))).toBe(true);
    }
  });

  it("gives out only on frames far below any real capture, and then by the side gate", () => {
    // A 10% inset of a 10px frame leaves an 8px side — exactly MIN_WARP_SIDE_PX.
    expect(isWarpableQuad(insetQuad(10, 10))).toBe(true);
    expect(isWarpableQuad(insetQuad(9, 9))).toBe(false);
  });
});

/**
 * True if the quad's two pairs of opposite edges cross — i.e. it is a bow-tie.
 * A simple (non-self-intersecting) quad has TL->TR never crossing BR->BL, and
 * TR->BR never crossing BL->TL.
 */
function isSelfIntersecting(q: Quad): boolean {
  const seg = (a: Pt, b: Pt) => ({ a, b });
  const cross = (o: Pt, a: Pt, b: Pt) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const crosses = (s1: { a: Pt; b: Pt }, s2: { a: Pt; b: Pt }) => {
    const d1 = cross(s1.a, s1.b, s2.a);
    const d2 = cross(s1.a, s1.b, s2.b);
    const d3 = cross(s2.a, s2.b, s1.a);
    const d4 = cross(s2.a, s2.b, s1.b);
    return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
  };
  return (
    crosses(seg(q.topLeft, q.topRight), seg(q.bottomRight, q.bottomLeft)) ||
    crosses(seg(q.topRight, q.bottomRight), seg(q.bottomLeft, q.topLeft))
  );
}

describe("polygonArea", () => {
  it("computes the area of a unit square", () => {
    expect(polygonArea([{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }])).toBe(1);
  });

  it("is winding-agnostic — clockwise and counter-clockwise agree", () => {
    const cw = [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 2 }, { x: 0, y: 2 }];
    const ccw = [...cw].reverse();
    expect(polygonArea(cw)).toBe(8);
    expect(polygonArea(ccw)).toBe(8);
  });

  it("returns 0 for a degenerate collinear polygon", () => {
    expect(polygonArea([{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }, { x: 3, y: 3 }])).toBe(0);
  });
});

describe("convexHullArea", () => {
  it("equals the polygon area for a convex quad", () => {
    const pts = [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 2 }, { x: 0, y: 2 }];
    expect(convexHullArea(pts)).toBeCloseTo(polygonArea(pts), 10);
  });

  it("exceeds the polygon area when a point is pushed inward", () => {
    // Concave "arrowhead": (2, 0.5) sits strictly inside the hull of the other
    // three, so the hull is larger than the polygon itself. (At (2, 1) the point
    // would land exactly ON the hull edge and the areas would tie.)
    const concave = [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 2, y: 0.5 }, { x: 0, y: 2 }];
    expect(convexHullArea(concave)).toBeGreaterThan(polygonArea(concave));
  });
});

describe("dist / lerp", () => {
  it("measures a 3-4-5 triangle", () => {
    expect(dist({ x: 0, y: 0 }, { x: 3, y: 4 })).toBe(5);
  });

  it("interpolates endpoints and midpoint", () => {
    const a = { x: 0, y: 0 };
    const b = { x: 10, y: 20 };
    expect(lerp(a, b, 0)).toEqual(a);
    expect(lerp(a, b, 1)).toEqual(b);
    expect(lerp(a, b, 0.5)).toEqual({ x: 5, y: 10 });
  });
});

describe("interiorAngles", () => {
  it("gives four right angles for a rectangle", () => {
    const angles = interiorAngles([{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 2 }, { x: 0, y: 2 }]);
    for (const a of angles) expect(a).toBeCloseTo(90, 6);
  });

  it("returns 0 for a duplicated vertex rather than NaN", () => {
    const angles = interiorAngles([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }]);
    expect(angles.every((a) => Number.isFinite(a))).toBe(true);
  });
});

describe("orderQuadByAngle", () => {
  const TL = { x: 10, y: 20 };
  const TR = { x: 90, y: 20 };
  const BR = { x: 90, y: 80 };
  const BL = { x: 10, y: 80 };

  it("labels an already-ordered rectangle unchanged", () => {
    expect(orderQuadByAngle([TL, TR, BR, BL])).toEqual({
      topLeft: TL, topRight: TR, bottomRight: BR, bottomLeft: BL,
    });
  });

  it("normalizes every permutation of the same rectangle to the same quad", () => {
    // Any order the four handles happen to be in must produce one canonical quad.
    const perms: Pt[][] = [
      [TR, BL, TL, BR],
      [BR, TL, BL, TR],
      [BL, BR, TR, TL],
      [TL, BR, TR, BL],
    ];
    for (const p of perms) {
      expect(orderQuadByAngle(p)).toEqual({
        topLeft: TL, topRight: TR, bottomRight: BR, bottomLeft: BL,
      });
    }
  });

  it("REPAIRS a bow-tie: dragging TL past BR relabels instead of self-intersecting", () => {
    // This is the critical-gap fix. Feeding the points in a crossing order used
    // to produce a folded quad that scanic would unwarp into a mirrored, small,
    // non-zero canvas — passing the width > 0 guard and silently reaching the
    // LLM as a mangled crop.
    const bowTie = [BR, TR, TL, BL]; // TL and BR swapped relative to the ring
    const repaired = orderQuadByAngle(bowTie);
    expect(isSelfIntersecting(repaired)).toBe(false);
    expect(repaired).toEqual({ topLeft: TL, topRight: TR, bottomRight: BR, bottomLeft: BL });
  });

  it("never emits a self-intersecting quad for randomised inputs", () => {
    let seed = 42;
    const rand = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed / 0x7fffffff;
    };
    for (let i = 0; i < 300; i++) {
      const pts = Array.from({ length: 4 }, () => ({ x: rand() * 1000, y: rand() * 1000 }));
      expect(isSelfIntersecting(orderQuadByAngle(pts))).toBe(false);
    }
  });

  it("preserves the four input points — repair relabels, it never invents geometry", () => {
    const pts = [{ x: 5, y: 90 }, { x: 70, y: 10 }, { x: 12, y: 15 }, { x: 88, y: 95 }];
    const out = quadPts(orderQuadByAngle(pts));
    for (const p of pts) {
      expect(out.some((o) => o.x === p.x && o.y === p.y)).toBe(true);
    }
    expect(out).toHaveLength(4);
  });

  it("handles a rotated diamond without collapsing it", () => {
    const diamond = [{ x: 50, y: 0 }, { x: 100, y: 50 }, { x: 50, y: 100 }, { x: 0, y: 50 }];
    const q = orderQuadByAngle(diamond);
    expect(isSelfIntersecting(q)).toBe(false);
    expect(polygonArea(quadPts(q))).toBeCloseTo(5000, 6);
  });

  it("throws on the wrong number of points", () => {
    expect(() => orderQuadByAngle([TL, TR, BR])).toThrow(/exactly 4 points, got 3/);
    expect(() => orderQuadByAngle([])).toThrow(/got 0/);
  });
});

describe("quadAreaFraction", () => {
  const full: Quad = {
    topLeft: { x: 0, y: 0 },
    topRight: { x: 100, y: 0 },
    bottomRight: { x: 100, y: 200 },
    bottomLeft: { x: 0, y: 200 },
  };

  it("is 1 when the quad fills the frame", () => {
    expect(quadAreaFraction(full, 100, 200)).toBeCloseTo(1, 10);
  });

  it("is 0.25 for a half-width, half-height quad", () => {
    const half: Quad = {
      topLeft: { x: 0, y: 0 },
      topRight: { x: 50, y: 0 },
      bottomRight: { x: 50, y: 100 },
      bottomLeft: { x: 0, y: 100 },
    };
    expect(quadAreaFraction(half, 100, 200)).toBeCloseTo(0.25, 10);
  });

  it("falls under the 5% review guard for a tiny quad", () => {
    const tiny: Quad = {
      topLeft: { x: 10, y: 10 },
      topRight: { x: 30, y: 10 },
      bottomRight: { x: 30, y: 30 },
      bottomLeft: { x: 10, y: 30 },
    };
    expect(quadAreaFraction(tiny, 1000, 1000)).toBeLessThan(0.05);
  });

  it("returns 0 rather than NaN for a zero-size frame", () => {
    expect(quadAreaFraction(full, 0, 0)).toBe(0);
    expect(quadAreaFraction(full, -5, 100)).toBe(0);
  });
});

describe("clampPtToFrame", () => {
  it("leaves an interior point untouched", () => {
    expect(clampPtToFrame({ x: 50, y: 60 }, 100, 200)).toEqual({ x: 50, y: 60 });
  });

  it("clamps a point dragged off every edge", () => {
    expect(clampPtToFrame({ x: -20, y: -30 }, 100, 200)).toEqual({ x: 0, y: 0 });
    expect(clampPtToFrame({ x: 999, y: 999 }, 100, 200)).toEqual({ x: 100, y: 200 });
  });

  it("keeps points exactly on the boundary", () => {
    expect(clampPtToFrame({ x: 0, y: 200 }, 100, 200)).toEqual({ x: 0, y: 200 });
  });
});

const mkQuad = (x = 0, y = 0): Quad => ({
  topLeft: { x, y },
  topRight: { x: x + 100, y },
  bottomRight: { x: x + 100, y: y + 200 },
  bottomLeft: { x, y: y + 200 },
});

describe("quadsEqual — the replacement for object identity", () => {
  it("matches two structurally identical but distinct objects", () => {
    const a = mkQuad();
    const b = mkQuad();
    expect(a).not.toBe(b); // distinct objects, the whole point
    expect(quadsEqual(a, b)).toBe(true);
  });

  it("matches a quad against itself", () => {
    const a = mkQuad();
    expect(quadsEqual(a, a)).toBe(true);
  });

  it("rejects a quad with any single corner moved", () => {
    const a = mkQuad();
    for (const k of ["topLeft", "topRight", "bottomRight", "bottomLeft"] as const) {
      const b = { ...mkQuad(), [k]: { x: 999, y: 999 } };
      expect(quadsEqual(a, b)).toBe(false);
    }
  });

  it("tolerates float drift from a scale-and-back round trip", () => {
    const a = mkQuad();
    const roundTripped = scaleQuadTwice(a, 1 / 3);
    expect(quadsEqual(a, roundTripped)).toBe(true);
  });

  it("rejects a difference larger than epsilon", () => {
    const a = mkQuad();
    const b = { ...mkQuad(), topLeft: { x: 0.01, y: 0 } };
    expect(quadsEqual(a, b)).toBe(false);
    expect(quadsEqual(a, b, 0.1)).toBe(true);
  });
});

/** Scale down then back up, so float error is real but tiny. */
function scaleQuadTwice(q: Quad, k: number): Quad {
  const down = {
    topLeft: { x: q.topLeft.x * k, y: q.topLeft.y * k },
    topRight: { x: q.topRight.x * k, y: q.topRight.y * k },
    bottomRight: { x: q.bottomRight.x * k, y: q.bottomRight.y * k },
    bottomLeft: { x: q.bottomLeft.x * k, y: q.bottomLeft.y * k },
  };
  return {
    topLeft: { x: down.topLeft.x / k, y: down.topLeft.y / k },
    topRight: { x: down.topRight.x / k, y: down.topRight.y / k },
    bottomRight: { x: down.bottomRight.x / k, y: down.bottomRight.y / k },
    bottomLeft: { x: down.bottomLeft.x / k, y: down.bottomLeft.y / k },
  };
}

describe("scaleDetectionResult", () => {
  const base = (over: Partial<DetectionResult> = {}): DetectionResult => ({
    corners: mkQuad(),
    score: 0.7,
    candidates: [{ quad: mkQuad(), score: 0.7 }],
    timingMs: 12,
    outcome: "accepted",
    ...over,
  });

  it("is a no-op at k === 1, returning the same object", () => {
    const r = base();
    expect(scaleDetectionResult(r, 1)).toBe(r);
  });

  it("scales corners and candidates by the same factor", () => {
    const out = scaleDetectionResult(base(), 2);
    expect(out.corners!.bottomRight).toEqual({ x: 200, y: 400 });
    expect(out.candidates![0].quad.bottomRight).toEqual({ x: 200, y: 400 });
  });

  // A rejected result: corners null, but the candidate still carries the quad
  // scanic found. Leaving it in detector space would misplace the near-miss
  // outline the Lab draws from it.
  it("scales the candidate of a REJECTED result, leaving corners null", () => {
    const out = scaleDetectionResult(base({ corners: null, outcome: "rejected-convexity" }), 2);
    expect(out.corners).toBeNull();
    expect(out.candidates![0].quad.bottomRight).toEqual({ x: 200, y: 400 });
    expect(out.outcome).toBe("rejected-convexity");
  });

  it("survives an absent candidates array", () => {
    const out = scaleDetectionResult(base({ candidates: undefined }), 2);
    expect(out.candidates).toBeUndefined();
    expect(out.corners!.bottomRight).toEqual({ x: 200, y: 400 });
  });

  it("passes score, timingMs, outcome and error through untouched", () => {
    const out = scaleDetectionResult(base({ error: "boom", outcome: "error" }), 2);
    expect(out.score).toBe(0.7);
    expect(out.timingMs).toBe(12);
    expect(out.outcome).toBe("error");
    expect(out.error).toBe("boom");
  });

  it("does not mutate its input", () => {
    const r = base();
    scaleDetectionResult(r, 2);
    expect(r.corners!.bottomRight).toEqual({ x: 100, y: 200 });
    expect(r.candidates![0].quad.bottomRight).toEqual({ x: 100, y: 200 });
  });
});

describe("insetQuad — the fallback crop when detection found nothing", () => {
  it("insets symmetrically by the given fraction", () => {
    expect(insetQuad(1000, 500, 0.1)).toEqual({
      topLeft: { x: 100, y: 50 },
      topRight: { x: 900, y: 50 },
      bottomRight: { x: 900, y: 450 },
      bottomLeft: { x: 100, y: 450 },
    });
  });

  it("defaults to a 10% inset", () => {
    expect(insetQuad(1000, 500)).toEqual(insetQuad(1000, 500, 0.1));
  });

  it("scales with the frame rather than using fixed pixels", () => {
    const small = insetQuad(100, 100);
    const large = insetQuad(1000, 1000);
    expect(large.topLeft.x).toBe(small.topLeft.x * 10);
  });

  it("is wound TL, TR, BR, BL — the order the review screen expects", () => {
    const q = insetQuad(1000, 500);
    expect(q.topLeft.x).toBeLessThan(q.topRight.x);
    expect(q.topLeft.y).toBeLessThan(q.bottomLeft.y);
    expect(q.bottomRight.x).toBeGreaterThan(q.bottomLeft.x);
  });

  /**
   * Winding is load-bearing downstream (`polygonClip` defines "inside" by it, so
   * a counter-wound quad silently scores IoU 0). `f` is exported API, and at
   * f >= 0.5 the inset crosses over itself: at 0.5 all four corners collapse to
   * the centre, above it topLeft.x overtakes topRight.x.
   */
  it("stays correctly wound across the usable range of f", () => {
    for (const f of [0, 0.05, 0.1, 0.25, 0.45]) {
      const q = insetQuad(1000, 500, f);
      expect(q.topLeft.x).toBeLessThan(q.topRight.x);
      expect(q.topLeft.y).toBeLessThan(q.bottomLeft.y);
    }
  });

  it("returns the full frame at f = 0", () => {
    expect(insetQuad(1000, 500, 0)).toEqual({
      topLeft: { x: 0, y: 0 },
      topRight: { x: 1000, y: 0 },
      bottomRight: { x: 1000, y: 500 },
      bottomLeft: { x: 0, y: 500 },
    });
  });
});

describe("quadFromPlacedPoints — four taps to a crop quad", () => {
  const tl = { x: 100, y: 100 };
  const tr = { x: 900, y: 100 };
  const br = { x: 900, y: 500 };
  const bl = { x: 100, y: 500 };

  it("builds the quad from four points tapped in order", () => {
    expect(quadFromPlacedPoints([tl, tr, br, bl], 1000, 600)).toEqual({
      topLeft: tl, topRight: tr, bottomRight: br, bottomLeft: bl,
    });
  });

  /**
   * The guarantee the on-screen prompt depends on: it says "order does not
   * matter", and orderQuadByAngle is what makes that true.
   */
  it("repairs any tap ORDER to the same quad", () => {
    const expected = { topLeft: tl, topRight: tr, bottomRight: br, bottomLeft: bl };
    const orders = [
      [br, bl, tl, tr],
      [tr, tl, bl, br],
      [bl, br, tr, tl],
      [tl, br, tr, bl], // diagonal-first, the order that would make a bow-tie
    ];
    for (const o of orders) {
      expect(quadFromPlacedPoints(o, 1000, 600)).toEqual(expected);
    }
  });

  /**
   * The review overlay letterboxes with `xMidYMid meet`, so there is margin
   * around the picture that maps to coordinates outside it. A tap there must
   * not place a corner off-frame.
   */
  it("clamps taps that land on the letterbox margin", () => {
    const q = quadFromPlacedPoints(
      [{ x: -500, y: -500 }, { x: 9999, y: -20 }, { x: 9999, y: 9999 }, { x: -30, y: 9999 }],
      1000,
      600,
    )!;
    for (const p of quadPts(q)) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(1000);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(600);
    }
  });

  it("refuses to build a quad from fewer or more than four points", () => {
    expect(quadFromPlacedPoints([], 1000, 600)).toBeNull();
    expect(quadFromPlacedPoints([tl], 1000, 600)).toBeNull();
    expect(quadFromPlacedPoints([tl, tr, br], 1000, 600)).toBeNull();
    expect(quadFromPlacedPoints([tl, tr, br, bl, tl], 1000, 600)).toBeNull();
  });

  it("never produces a self-intersecting quad from four DISTINCT points", () => {
    // A deterministic sweep, not one hand-picked permutation. The original
    // version of this test asserted the property for a single input and so
    // could not see the coincident-point case below.
    let seed = 12345;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
    for (let i = 0; i < 300; i++) {
      const pts = Array.from({ length: 4 }, () => ({ x: rnd() * 1000, y: rnd() * 600 }));
      // Skip near-coincident draws; those are `advancePlacement`'s job, below.
      const tooClose = pts.some((a, ai) =>
        pts.some((b, bi) => ai !== bi && Math.hypot(a.x - b.x, a.y - b.y) < 20),
      );
      if (tooClose) continue;
      expect(isSelfIntersecting(quadFromPlacedPoints(pts, 1000, 600)!)).toBe(false);
    }
  });

  /**
   * The limit of what ordering can fix, stated so nobody assumes otherwise.
   * `orderQuadByAngle` sorts by atan2 around the centroid; two coincident points
   * tie, the sort keeps their input order, and the quad folds. Preventing this
   * is `advancePlacement`'s job, not this function's.
   */
  it("CANNOT repair coincident points — the guard belongs upstream", () => {
    const dup = { x: 100, y: 100 };
    const folded = quadFromPlacedPoints([dup, dup, { x: 900, y: 100 }, { x: 500, y: 500 }], 1000, 600)!;
    expect(isSelfIntersecting(folded)).toBe(true);
  });

  /**
   * Pins the ORDER of operations, which the bounds-only clamp test above cannot
   * see. Clamping moves the centroid, which moves the angular sort, so
   * clamp-then-order and order-then-clamp disagree on asymmetric off-frame
   * input. This input is one where they differ.
   */
  it("clamps BEFORE ordering, not after", () => {
    const q = quadFromPlacedPoints(
      [{ x: 1614, y: -231 }, { x: 835, y: 196 }, { x: 1337, y: 849 }, { x: 424, y: 1 }],
      1000,
      600,
    )!;
    const clampedThenOrdered = orderQuadByAngle(
      [{ x: 1614, y: -231 }, { x: 835, y: 196 }, { x: 1337, y: 849 }, { x: 424, y: 1 }].map((p) =>
        clampPtToFrame(p, 1000, 600),
      ),
    );
    expect(q).toEqual(clampedThenOrdered);
  });
});

describe("advancePlacement — one tap at a time", () => {
  const W = 1000;
  const H = 600;
  const a = { x: 100, y: 100 };
  const b = { x: 900, y: 100 };
  const c = { x: 900, y: 500 };
  const d = { x: 100, y: 500 };

  it("accumulates taps 1 through 3 without committing", () => {
    let placed: Pt[] = [];
    for (const [i, p] of [a, b, c].entries()) {
      const step = advancePlacement(placed, p, W, H);
      expect(step.commit).toBeNull();
      expect(step.placed).toHaveLength(i + 1);
      placed = step.placed;
    }
  });

  it("commits on the fourth tap and resets, so length is never 4", () => {
    const step = advancePlacement([a, b, c], d, W, H);
    expect(step.commit).not.toBeNull();
    expect(step.placed).toEqual([]);
  });

  /**
   * THE regression. A double-tap at rest yields two identical clientX/clientY,
   * hence two identical frame points. Ordering cannot separate them and the quad
   * folds into a triangle measuring ~27% of the frame — which clears
   * MIN_AREA_FRACTION, so the "almost empty" escape never fires and the user
   * silently gets a wrong crop.
   */
  it("IGNORES a tap coincident with one already placed", () => {
    const step = advancePlacement([a], { ...a }, W, H);
    expect(step.placed).toEqual([a]);
    expect(step.commit).toBeNull();
  });

  it("ignores a tap merely too CLOSE, not just exactly coincident", () => {
    const gap = Math.min(W, H) * MIN_TAP_SEPARATION_FRACTION;
    const near = { x: a.x + gap * 0.5, y: a.y };
    expect(advancePlacement([a], near, W, H).placed).toEqual([a]);
    const far = { x: a.x + gap * 1.5, y: a.y };
    expect(advancePlacement([a], far, W, H).placed).toHaveLength(2);
  });

  it("never commits a self-intersecting quad, however the taps land", () => {
    let seed = 999;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
    for (let i = 0; i < 300; i++) {
      let placed: Pt[] = [];
      let commit = null;
      // Deliberately include repeats so the coincident path is exercised.
      const taps = Array.from({ length: 12 }, () =>
        rnd() < 0.3 ? { x: 300, y: 300 } : { x: rnd() * W, y: rnd() * H },
      );
      for (const t of taps) {
        const step = advancePlacement(placed, t, W, H);
        placed = step.placed;
        if (step.commit) { commit = step.commit; break; }
      }
      if (commit) expect(isSelfIntersecting(commit)).toBe(false);
    }
  });

  it("clamps a tap on the letterbox margin instead of dropping it", () => {
    const step = advancePlacement([], { x: -400, y: -400 }, W, H);
    expect(step.placed).toEqual([{ x: 0, y: 0 }]);
  });
});
