/**
 * Pure quad geometry for the scanner.
 *
 * DOM-free by contract: this module must never import `canvas-utils.ts` or
 * reference `document`, `ImageData`, or any other browser global, so the vitest
 * suite can run it in plain Node with no jsdom and no `canvas` package.
 *
 * All coordinates are screen/image convention: x grows right, y grows DOWN.
 * A quad written TL -> TR -> BR -> BL therefore winds clockwise on screen.
 */

import type { Pt, Quad } from "./detector";

/** Shoelace area of an arbitrary polygon (absolute value, so winding-agnostic). */
export function polygonArea(pts: Pt[]): number {
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const j = (i + 1) % pts.length;
    a += pts[i].x * pts[j].y - pts[j].x * pts[i].y;
  }
  return Math.abs(a) / 2;
}

/** Area of the convex hull of the points (monotone chain). */
export function convexHullArea(pts: Pt[]): number {
  const sorted = [...pts].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o: Pt, a: Pt, b: Pt) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: Pt[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Pt[] = [];
  for (let i = sorted.length - 1; i >= 0; i--) {
    const p = sorted[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return polygonArea([...lower.slice(0, -1), ...upper.slice(0, -1)]);
}

/** Euclidean distance. */
export function dist(a: Pt, b: Pt): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Linear interpolation between two points, t in [0,1]. */
export function lerp(a: Pt, b: Pt, t: number): Pt {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
}

/** Interior angle at each vertex, in degrees, in the polygon's own order. */
export function interiorAngles(pts: Pt[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    const prev = pts[(i + pts.length - 1) % pts.length];
    const cur = pts[i];
    const next = pts[(i + 1) % pts.length];
    const v1 = { x: prev.x - cur.x, y: prev.y - cur.y };
    const v2 = { x: next.x - cur.x, y: next.y - cur.y };
    const dot = v1.x * v2.x + v1.y * v2.y;
    const mag = Math.hypot(v1.x, v1.y) * Math.hypot(v2.x, v2.y);
    if (mag === 0) {
      out.push(0);
      continue;
    }
    const cos = Math.max(-1, Math.min(1, dot / mag));
    out.push((Math.acos(cos) * 180) / Math.PI);
  }
  return out;
}

/**
 * Repair + relabel four points into a TL/TR/BR/BL quad.
 *
 * This is what makes a self-intersecting quad impossible to represent: dragging
 * the top-left handle past the bottom-right does not produce a bow-tie, it
 * simply relabels which corner is which.
 *
 * Winding assumption: y grows DOWNWARD (screen/image coordinates), so sorting
 * by ascending `atan2(y - cy, x - cx)` walks the points CLOCKWISE as seen on
 * screen, and the emitted TL -> TR -> BR -> BL order is clockwise too. In a
 * y-up (mathematical) coordinate system the same sort walks counter-clockwise
 * and the "top" pair would be the larger-y pair instead — do not reuse this
 * function there without flipping the y comparison.
 *
 * Steps:
 *  1. Sort the four points by angle around their centroid. The result is a
 *     simple (non-self-intersecting) ring, clockwise on screen.
 *  2. ROTATE that ring so it starts at the most top-left point, and read the
 *     labels straight off it: TL, TR, BR, BL.
 *
 * Step 2 must be a rotation, never a re-sort. An earlier version relabelled by
 * y-position (smaller-y pair = top, larger-y pair = bottom), which discards the
 * ring order — and for quads where three points sit near the top, the two
 * "top" points are not adjacent in the ring, so the relabelling reintroduced a
 * crossing. A randomised test caught it. Rotating preserves adjacency, so the
 * no-self-intersection guarantee actually holds.
 *
 * Ties are broken deterministically (angle -> radius -> x -> y for the sort;
 * x+y -> y -> x -> index for the start point), so the same input always yields
 * the same labelling.
 *
 * Throws if `pts` does not contain exactly four points.
 */
export function orderQuadByAngle(pts: Pt[]): Quad {
  if (pts.length !== 4) {
    throw new Error(`orderQuadByAngle expects exactly 4 points, got ${pts.length}`);
  }

  const cx = (pts[0].x + pts[1].x + pts[2].x + pts[3].x) / 4;
  const cy = (pts[0].y + pts[1].y + pts[2].y + pts[3].y) / 4;

  const ring = [...pts].sort((a, b) => {
    const angA = Math.atan2(a.y - cy, a.x - cx);
    const angB = Math.atan2(b.y - cy, b.x - cx);
    if (angA !== angB) return angA - angB;
    // Same ray from the centroid: nearer point first, then x, then y.
    const rA = Math.hypot(a.x - cx, a.y - cy);
    const rB = Math.hypot(b.x - cx, b.y - cy);
    return rA - rB || a.x - b.x || a.y - b.y;
  });

  // Rotate the ring to start at the most top-left point (minimum x + y).
  let startIdx = 0;
  for (let i = 1; i < 4; i++) {
    const cur = ring[i];
    const best = ring[startIdx];
    const curScore = cur.x + cur.y;
    const bestScore = best.x + best.y;
    if (curScore < bestScore || (curScore === bestScore && (cur.y < best.y || (cur.y === best.y && cur.x < best.x)))) {
      startIdx = i;
    }
  }
  const at = (i: number) => {
    const p = ring[(startIdx + i) % 4];
    return { x: p.x, y: p.y };
  };

  return { topLeft: at(0), topRight: at(1), bottomRight: at(2), bottomLeft: at(3) };
}

/**
 * Fraction of the frame covered by the quad, in [0, ~1].
 * Used by the review screen's "too small to be a crop" guard.
 */
export function quadAreaFraction(quad: Quad, frameW: number, frameH: number): number {
  const frameArea = frameW * frameH;
  if (!(frameArea > 0)) return 0;
  const area = polygonArea([quad.topLeft, quad.topRight, quad.bottomRight, quad.bottomLeft]);
  return area / frameArea;
}

/** Clamp a point into the [0,frameW] x [0,frameH] rectangle. */
export function clampPtToFrame(p: Pt, frameW: number, frameH: number): Pt {
  return {
    x: Math.max(0, Math.min(frameW, p.x)),
    y: Math.max(0, Math.min(frameH, p.y)),
  };
}

/**
 * Multiply every corner by one uniform ratio. This is how a quad crosses
 * between the coordinate spaces in the header of `detector.ts`:
 *
 *   detection -> video : scaleQuad(q, 1 / detectionScale)
 *   downscaled -> raw  : scaleQuad(q, 1 / downscaleResult.scale)
 *
 * ONE ratio, both axes — never a separate x and y factor. Independent per-axis
 * scaling over an `object-cover` video is the exact bug this whole subsystem
 * was rewritten to fix.
 */
export function scaleQuad(q: Quad, k: number): Quad {
  return {
    topLeft: { x: q.topLeft.x * k, y: q.topLeft.y * k },
    topRight: { x: q.topRight.x * k, y: q.topRight.y * k },
    bottomRight: { x: q.bottomRight.x * k, y: q.bottomRight.y * k },
    bottomLeft: { x: q.bottomLeft.x * k, y: q.bottomLeft.y * k },
  };
}

/** Clamp all four corners into the frame. */
export function clampQuad(q: Quad, frameW: number, frameH: number): Quad {
  return {
    topLeft: clampPtToFrame(q.topLeft, frameW, frameH),
    topRight: clampPtToFrame(q.topRight, frameW, frameH),
    bottomRight: clampPtToFrame(q.bottomRight, frameW, frameH),
    bottomLeft: clampPtToFrame(q.bottomLeft, frameW, frameH),
  };
}

/** Normalize a quad to 0-1 against a frame, matching how ground truth is stored. */
export function normalizeQuad(q: Quad, frameW: number, frameH: number): Quad {
  if (!(frameW > 0) || !(frameH > 0)) return q;
  return {
    topLeft: { x: q.topLeft.x / frameW, y: q.topLeft.y / frameH },
    topRight: { x: q.topRight.x / frameW, y: q.topRight.y / frameH },
    bottomRight: { x: q.bottomRight.x / frameW, y: q.bottomRight.y / frameH },
    bottomLeft: { x: q.bottomLeft.x / frameW, y: q.bottomLeft.y / frameH },
  };
}

/**
 * Sutherland-Hodgman polygon clip. Returns the intersection of `subject` with
 * the CONVEX polygon `clip`.
 *
 * WINDING IS LOAD-BEARING: "inside" is defined as `isLeft(A, B, P) >= 0`, which
 * is only correct when `clip` winds the same way as the TL -> TR -> BR -> BL
 * convention (clockwise in screen coordinates, where y grows down). Hand it a
 * counter-wound polygon and every point reads as outside, so the result is
 * empty and the IoU comes back 0 — indistinguishable from a total miss rather
 * than surfacing as an error.
 *
 * That is not hypothetical: this function produced the median-IoU numbers that
 * killed the 2026-07-03 DocAligner design. Those numbers survive scrutiny (the
 * spike's high-confidence frames scored IoU 0.91-0.96, which is impossible under
 * inverted winding), but the failure mode is silent, so `quadIoU` is covered by
 * winding regression tests. Run `orderQuadByAngle` on anything of uncertain
 * provenance before measuring with it.
 */
export function polygonClip(subject: Pt[], clip: Pt[]): Pt[] {
  let output = subject.slice();
  for (let i = 0; i < clip.length; i++) {
    if (output.length === 0) break;
    const input = output;
    output = [];
    const A = clip[i];
    const B = clip[(i + 1) % clip.length];
    for (let j = 0; j < input.length; j++) {
      const P = input[j];
      const Q = input[(j + 1) % input.length];
      const Pin = isLeft(A, B, P) >= 0;
      const Qin = isLeft(A, B, Q) >= 0;
      if (Pin) {
        output.push(P);
        if (!Qin) output.push(intersect(P, Q, A, B));
      } else if (Qin) {
        output.push(intersect(P, Q, A, B));
      }
    }
  }
  return output;
}

/**
 * Intersection-over-union of two quads. This is the scanner's accuracy metric:
 * every "median IoU" and "hit@0.85" number in the design docs comes from here.
 * See the winding note on `polygonClip`.
 */
export function quadIoU(a: Quad, b: Quad): number {
  const polyA = [a.topLeft, a.topRight, a.bottomRight, a.bottomLeft];
  const polyB = [b.topLeft, b.topRight, b.bottomRight, b.bottomLeft];
  const interArea = polygonArea(polygonClip(polyA, polyB));
  const union = polygonArea(polyA) + polygonArea(polyB) - interArea;
  return union > 0 ? interArea / union : 0;
}

/** Signed cross product: > 0 when P is left of the directed line A -> B. */
function isLeft(A: Pt, B: Pt, P: Pt): number {
  return (B.x - A.x) * (P.y - A.y) - (B.y - A.y) * (P.x - A.x);
}

function intersect(P: Pt, Q: Pt, A: Pt, B: Pt): Pt {
  const r = { x: Q.x - P.x, y: Q.y - P.y };
  const s = { x: B.x - A.x, y: B.y - A.y };
  const denom = r.x * s.y - r.y * s.x;
  if (Math.abs(denom) < 1e-9) return P;
  const t = ((A.x - P.x) * s.y - (A.y - P.y) * s.x) / denom;
  return { x: P.x + t * r.x, y: P.y + t * r.y };
}
