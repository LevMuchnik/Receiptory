import { describe, it, expect } from "vitest";
import {
  CLASSICAL_DEFAULTS,
  classifyScanResult,
  firstHardReject,
  scanThrowMessage,
  toQuad,
  type Metrics,
} from "./classical-detector";

/**
 * The silent-failure suite.
 *
 * Two decisions in this file were made by hand, both reversing a prior
 * position, and both carry comments describing catastrophic silent failures.
 * Neither had a test. That is exactly the shape of a thing someone reverts in
 * six months because the comment reads like an over-cautious opinion.
 *
 * `detect()` itself needs a canvas (preprocessing blurs through the DOM), so
 * the decisions were extracted into pure functions to make them reachable here.
 */

describe("classifyScanResult — the error-badge decision", () => {
  it("STAYS SILENT on scanic's default empty-frame message", () => {
    // THE load-bearing case. In detect mode scanic returns success:false with
    // "No document detected" whenever zero contours clear minArea — i.e. on
    // every frame of a bare table, and every frame caught mid-motion. An
    // eng-review outside voice called this "the dominant half-broken case" and
    // the plan told us to flag it as an error; reading scanic's source proved
    // that wrong. Flagging it would pin the error badge on permanently and
    // destroy the one signal meaning "the detector is broken".
    const c = classifyScanResult({ success: false, message: "No document detected" });
    expect(c.error).toBeUndefined();
    expect(c.rawCorners).toBeNull();
  });

  it("stays silent when success:false carries no message at all", () => {
    const c = classifyScanResult({ success: false });
    expect(c.error).toBeUndefined();
  });

  it("SURFACES a success:false message scanic does not normally emit", () => {
    const c = classifyScanResult({ success: false, message: "WASM heap exhausted" });
    expect(c.error).toBe("Scanner: WASM heap exhausted");
  });

  it("returns corners untouched on a clean detection", () => {
    const corners = { topLeft: { x: 1, y: 2 } };
    const c = classifyScanResult({ success: true, corners });
    expect(c.rawCorners).toBe(corners);
    expect(c.error).toBeUndefined();
  });

  it("treats success:true with no corners as a failure", () => {
    // Undocumented shape. Better to shout than to render it as an empty table.
    const c = classifyScanResult({ success: true });
    expect(c.error).toBe("Scanner returned no corners");
  });

  it("treats a null/undefined result as a failure rather than an empty frame", () => {
    expect(classifyScanResult(null).error).toBe("Scanner returned no result");
    expect(classifyScanResult(undefined).error).toBe("Scanner returned no result");
  });
});

describe("scanThrowMessage", () => {
  it("formats an Error", () => {
    expect(scanThrowMessage(new Error("boom"))).toBe("Scanner error: boom");
  });

  it("formats a non-Error rejection without throwing", () => {
    expect(scanThrowMessage("string rejection")).toBe("Scanner error: string rejection");
    expect(scanThrowMessage(undefined)).toBe("Scanner error: undefined");
  });
});

describe("toQuad — the NaN guard", () => {
  const ok = {
    topLeft: { x: 1, y: 2 },
    topRight: { x: 3, y: 2 },
    bottomRight: { x: 3, y: 4 },
    bottomLeft: { x: 1, y: 4 },
  };

  it("accepts the object form", () => {
    expect(toQuad(ok)).toEqual(ok);
  });

  it("accepts the array form", () => {
    expect(toQuad([ok.topLeft, ok.topRight, ok.bottomRight, ok.bottomLeft])).toEqual(ok);
  });

  it("REJECTS NaN coordinates", () => {
    // typeof NaN === "number", so the old check let these through. Every
    // downstream guard is a `<` / `>` comparison and ALL of them are false for
    // NaN, so a NaN quad was ACCEPTED: hard rejects passed, the drift gate
    // never fired, the badge read "Document detected" over an empty screen,
    // and in review the handles became un-grabbable with no escape offered.
    expect(toQuad({ ...ok, topLeft: { x: NaN, y: 2 } })).toBeNull();
    expect(toQuad({ ...ok, bottomRight: { x: 3, y: NaN } })).toBeNull();
  });

  it("REJECTS Infinity coordinates", () => {
    expect(toQuad({ ...ok, topRight: { x: Infinity, y: 2 } })).toBeNull();
    expect(toQuad({ ...ok, bottomLeft: { x: 1, y: -Infinity } })).toBeNull();
  });

  it("rejects a missing corner", () => {
    expect(toQuad({ topLeft: ok.topLeft, topRight: ok.topRight, bottomRight: ok.bottomRight })).toBeNull();
    expect(toQuad([ok.topLeft, ok.topRight])).toBeNull();
  });

  it("rejects non-numeric coordinates", () => {
    expect(toQuad({ ...ok, topLeft: { x: "1", y: 2 } })).toBeNull();
    expect(toQuad({ ...ok, topLeft: { x: null, y: 2 } })).toBeNull();
  });

  it("returns null for null/undefined input", () => {
    expect(toQuad(null)).toBeNull();
    expect(toQuad(undefined)).toBeNull();
  });
});

/**
 * The hard rejects — the gates that turn "scanic found a quad" into
 * `corners: null` with no error, which the viewfinder renders identically to an
 * empty table. Zero coverage existed before 2026-09-05, on the branch that
 * changed them.
 *
 * `firstHardReject` takes `Metrics`, not `ImageData`, so it is pure and needs no
 * DOM — the whole reason it was extracted.
 */
describe("firstHardReject — which gate threw the quad away", () => {
  /** A quad that passes everything, so each test can spoil exactly one field. */
  const passing: Metrics = {
    area: 100_000,
    areaFraction: 0.4,
    convexity: 1,
    aspect: 2,
    minAngle: 85,
    maxAngle: 95,
    uniformity: 0.9,
    textDensity: 0.1,
    score: () => 0.8,
  };
  const p = CLASSICAL_DEFAULTS;

  it("accepts a well-formed quad", () => {
    expect(firstHardReject(passing, p)).toBeNull();
  });

  it("rejects a quad that is too small, and too large", () => {
    expect(firstHardReject({ ...passing, areaFraction: 0.05 }, p)).toBe("rejected-area");
    expect(firstHardReject({ ...passing, areaFraction: 0.99 }, p)).toBe("rejected-area");
  });

  it("rejects a quad longer than maxAspect", () => {
    expect(firstHardReject({ ...passing, aspect: 20 }, p)).toBe("rejected-aspect");
  });

  // The reason minAspect is NOT dead code. computeMetrics falls back to
  // `aspect = 0` when either mean side length is zero, and 0 < minAspect (0.4)
  // fires. A 2026-09-05 review called this bound unreachable and was wrong;
  // this test is what stops the next reader deleting it.
  it("rejects a DEGENERATE quad, whose aspect is 0 rather than >= 1", () => {
    expect(firstHardReject({ ...passing, aspect: 0 }, p)).toBe("rejected-aspect");
  });

  it("rejects corners outside the angle band, at both ends", () => {
    expect(firstHardReject({ ...passing, minAngle: 30 }, p)).toBe("rejected-angle");
    expect(firstHardReject({ ...passing, maxAngle: 150 }, p)).toBe("rejected-angle");
  });

  it("rejects a non-convex quad — the curled-receipt shape", () => {
    expect(firstHardReject({ ...passing, convexity: 0.5 }, p)).toBe("rejected-convexity");
  });

  it("reports the FIRST failure, not all of them", () => {
    const doomed = { ...passing, areaFraction: 0.01, aspect: 99, convexity: 0 };
    expect(firstHardReject(doomed, p)).toBe("rejected-area");
  });

  it("treats each bound as inclusive at the threshold itself", () => {
    expect(firstHardReject({ ...passing, areaFraction: p.minAreaFraction }, p)).toBeNull();
    expect(firstHardReject({ ...passing, aspect: p.maxAspect }, p)).toBeNull();
    expect(firstHardReject({ ...passing, minAngle: p.minAngleDeg }, p)).toBeNull();
    expect(firstHardReject({ ...passing, maxAngle: p.maxAngleDeg }, p)).toBeNull();
  });
});
