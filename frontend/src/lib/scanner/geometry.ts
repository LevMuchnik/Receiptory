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

import type { DetectionResult, Pt, Quad } from "./detector";

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

/**
 * Do two quads describe the same four corners?
 *
 * Exists so consumers can stop using object identity (`a === b`) to ask this.
 * Identity is a tempting dedupe key when a detector returns the same object in
 * both `corners` and `candidates[0].quad`, but it is a contract nothing in the
 * type system enforces: the moment any transform maps over a result, the two
 * become separate objects and the identity check silently stops matching. That
 * happened here on 2026-09-05 — a downscale-and-rescale step split them, and the
 * Lab overlay began drawing the accepted quad twice with no test able to catch
 * it, because the symptom is purely visual.
 *
 * Epsilon rather than exact equality: the quads being compared have usually been
 * through a float scale-and-back, so bitwise equality is not the question being
 * asked. The default is far tighter than a pixel.
 */
export function quadsEqual(a: Quad, b: Quad, epsilon = 1e-6): boolean {
  const keys = ["topLeft", "topRight", "bottomRight", "bottomLeft"] as const;
  return keys.every(
    (k) => Math.abs(a[k].x - b[k].x) <= epsilon && Math.abs(a[k].y - b[k].y) <= epsilon,
  );
}

/**
 * Scale every quad a detection result carries by `k`, leaving the rest intact.
 *
 * Pure: the caller does the canvas work, this does the arithmetic. Extracted so
 * the coordinate mapping is reachable from the node-env test suite — the same
 * reason `classifyScanResult` and `toQuad` were pulled out of `detect()`.
 *
 * `candidates` matters as much as `corners`: it carries the REJECTED quad, which
 * is the whole point of the reject breakdown, and leaving it in the detector's
 * space while `corners` moves to the caller's would put a landmine under the
 * first consumer that draws it.
 */
export function scaleDetectionResult(result: DetectionResult, k: number): DetectionResult {
  if (k === 1) return result;
  return {
    ...result,
    corners: result.corners ? scaleQuad(result.corners, k) : null,
    candidates: result.candidates?.map((c) => ({ ...c, quad: scaleQuad(c.quad, k) })),
  };
}

/**
 * The fallback crop when detection found nothing: a symmetric inset of the frame.
 *
 * Lives here rather than in `CaptureReview` so it is reachable from the node-env
 * suite — it is pure arithmetic, and the review screen leans on it for every
 * harsh capture, which is exactly the case that gets the least manual testing.
 */
export function insetQuad(w: number, h: number, f = 0.1): Quad {
  const x0 = w * f;
  const x1 = w * (1 - f);
  const y0 = h * f;
  const y1 = h * (1 - f);
  return {
    topLeft: { x: x0, y: y0 },
    topRight: { x: x1, y: y0 },
    bottomRight: { x: x1, y: y1 },
    bottomLeft: { x: x0, y: y1 },
  };
}

/** Shortest side, in the quad's own pixels, the perspective warp will accept. */
export const MIN_WARP_SIDE_PX = 8;

/**
 * Smallest |sin| of the turn at any corner. Below this the corner is flat enough
 * to make the homography numerically singular. 0.01 is about 0.6 degrees.
 */
const MIN_WARP_TURN_SIN = 0.01;

/** Do segments a-b and c-d properly cross (endpoints touching does not count)? */
function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const side = (o: Pt, p: Pt, q: Pt) => Math.sign((p.x - o.x) * (q.y - o.y) - (p.y - o.y) * (q.x - o.x));
  const d1 = side(a, b, c);
  const d2 = side(a, b, d);
  const d3 = side(c, d, a);
  const d4 = side(c, d, b);
  return d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0 && d1 !== d2 && d3 !== d4;
}

/**
 * Can this quad be handed to the perspective warp? It must be finite, have no
 * side shorter than `MIN_WARP_SIDE_PX`, no corner flat enough to make the
 * homography singular, and no crossing edges (a bow-tie).
 *
 * Why the warp needs this: scanic 1.6's extract rejects only an exactly-zero
 * determinant. Collinear or coincident corners give a singular solve whose
 * result is NaN, every output pixel truncates to 0, and it reports success:
 * a solid black page, filed as the receipt. Reproduced 2026-09-11 for three
 * corners on one frame edge, all four collinear, and two coincident corners,
 * all of which review's drag handles can produce by clamping to the frame.
 * Callers hand back the whole frame instead, the same policy as a failed warp.
 *
 * A CONCAVE quad — one handle dragged past the opposite diagonal — is NOT
 * refused, though an earlier revision of this guard demanded strict convexity.
 * It warps fine, oddly but deterministically, and the odd result is exactly
 * what review is drawing. Refusing it would file the whole frame while the
 * screen showed the dented box: the shown-vs-filed split this branch removed
 * from the no-detection path. Only shapes the warp cannot honour are refused.
 */
export function isWarpableQuad(q: Quad, minSide = MIN_WARP_SIDE_PX): boolean {
  const pts = [q.topLeft, q.topRight, q.bottomRight, q.bottomLeft];
  if (pts.some((p) => !p || !Number.isFinite(p.x) || !Number.isFinite(p.y))) return false;

  for (let i = 0; i < 4; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % 4];
    const c = pts[(i + 2) % 4];
    const ab = dist(a, b);
    const bc = dist(b, c);
    if (ab < minSide) return false;
    // |cross| / (|ab| * |bc|) is |sin(turn)|: scale-free, so a 4K quad and its
    // review-space preview get the same verdict.
    const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
    if (Math.abs(cross) < MIN_WARP_TURN_SIN * ab * bc) return false;
  }

  // Opposite edges crossing is a bow-tie: the quad folds through itself and the
  // warp has no coherent interior to sample.
  return !(
    segmentsCross(pts[0], pts[1], pts[2], pts[3]) || segmentsCross(pts[1], pts[2], pts[3], pts[0])
  );
}

/**
 * How close two placement taps may be before the second is ignored, as a
 * fraction of the frame's SHORTER edge.
 *
 * Coincident points are the failure this guards. `orderQuadByAngle` sorts by
 * `atan2` around the centroid, and two identical points tie; the sort keeps
 * their input order and the quad folds into a triangle with one doubled corner.
 * A folded quad still measures ~27% of the frame, which clears
 * `MIN_AREA_FRACTION`, so the review screen's "that crop is almost empty" escape
 * never fires and the user gets a silently wrong crop. A double-tap at rest
 * produces exactly this: two taps with identical clientX/clientY.
 */
export const MIN_TAP_SEPARATION_FRACTION = 0.02;

export interface PlacementStep {
  /** Points placed so far, after this tap. Unchanged if the tap was ignored. */
  placed: Pt[];
  /** Set only on the tap that completes the quad. */
  commit: Quad | null;
}

/**
 * Apply one placement tap.
 *
 * Pure, and extracted from the pointer handler for that reason: it is the only
 * place the coincident-tap guard can live where a test can reach it.
 *
 * Contract:
 * - a tap closer than `MIN_TAP_SEPARATION_FRACTION` to an already-placed point
 *   is IGNORED, returning `placed` unchanged so the caller renders no change
 * - taps 1-3 accumulate and commit nothing
 * - tap 4 commits and resets `placed` to empty, so `placed.length` is never 4
 *   (the prompt indexes on that length and would run off the end otherwise)
 */
export function advancePlacement(
  placed: Pt[],
  tap: Pt,
  frameW: number,
  frameH: number,
): PlacementStep {
  const p = clampPtToFrame(tap, frameW, frameH);
  const minGap = Math.min(frameW, frameH) * MIN_TAP_SEPARATION_FRACTION;
  if (placed.some((q) => dist(q, p) < minGap)) return { placed, commit: null };

  const next = [...placed, p];
  if (next.length < 4) return { placed: next, commit: null };
  return { placed: [], commit: quadFromPlacedPoints(next, frameW, frameH) };
}

/**
 * Turn four tapped points into a crop quad, or null if there are not four.
 *
 * Two guarantees the caller depends on, both delegated to code that already
 * exists and is already property-tested:
 *
 * - **Tap ORDER does not matter.** `orderQuadByAngle` sorts by angle around the
 *   centroid and relabels TL/TR/BR/BL, so a user who taps the corners in any
 *   sequence still gets a sane, non-self-intersecting quad. The on-screen
 *   prompts are guidance, not a constraint the user can violate.
 * - **Taps outside the image cannot escape the frame.** The review overlay
 *   letterboxes with `xMidYMid meet`, so there is margin around the picture that
 *   maps to coordinates outside it; every point is clamped before use.
 */
export function quadFromPlacedPoints(pts: Pt[], frameW: number, frameH: number): Quad | null {
  if (pts.length !== 4) return null;
  return orderQuadByAngle(pts.map((p) => clampPtToFrame(p, frameW, frameH)));
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
