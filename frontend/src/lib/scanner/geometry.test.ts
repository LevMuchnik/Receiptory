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
} from "./geometry";
import type { DetectionResult, Pt, Quad } from "./detector";

const quadPts = (q: Quad): Pt[] => [q.topLeft, q.topRight, q.bottomRight, q.bottomLeft];

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
