import { describe, it, expect } from "vitest";
import {
  CLASSICAL_DEFAULTS,
  classifyScanResult,
  firstHardReject,
  outcomeForNoQuad,
  scanThrowMessage,
  toQuad,
  type Metrics,
} from "./classical-detector";
import { REJECT_OUTCOMES, isRejectOutcome } from "./detector";

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
    expect(firstHardReject({ ...passing, areaFraction: p.maxAreaFraction }, p)).toBeNull();
    expect(firstHardReject({ ...passing, aspect: p.minAspect }, p)).toBeNull();
    expect(firstHardReject({ ...passing, aspect: p.maxAspect }, p)).toBeNull();
    expect(firstHardReject({ ...passing, minAngle: p.minAngleDeg }, p)).toBeNull();
    expect(firstHardReject({ ...passing, maxAngle: p.maxAngleDeg }, p)).toBeNull();
  });

  /**
   * The convexity floor is still a bare constant inside the module (promoting it
   * to a param is T10). Nothing else pins its value, so this test is what stops
   * that promotion silently moving the floor.
   */
  it("pins the convexity floor at 0.85, inclusive", () => {
    expect(firstHardReject({ ...passing, convexity: 0.85 }, p)).toBeNull();
    expect(firstHardReject({ ...passing, convexity: 0.8499 }, p)).toBe("rejected-convexity");
  });

  /**
   * The hazard `toQuad` documents, arriving through a different door. Every gate
   * is `x < t` / `x > t` and all NaN comparisons are false, so without an
   * explicit finite check a NaN metric passes all four and reports as accepted —
   * the viewfinder then draws nothing while the badge says a document was found.
   */
  it("REJECTS a non-finite metric rather than letting it pass every gate", () => {
    expect(firstHardReject({ ...passing, areaFraction: NaN }, p)).toBe("rejected-area");
    expect(firstHardReject({ ...passing, aspect: NaN }, p)).toBe("rejected-aspect");
    expect(firstHardReject({ ...passing, minAngle: NaN }, p)).toBe("rejected-angle");
    expect(firstHardReject({ ...passing, maxAngle: NaN }, p)).toBe("rejected-angle");
    expect(firstHardReject({ ...passing, convexity: NaN }, p)).toBe("rejected-convexity");
    expect(firstHardReject({ ...passing, areaFraction: Infinity }, p)).toBe("rejected-area");
  });

  it("reports every reject through the shared REJECT_OUTCOMES contract", () => {
    // A reject the Lab cannot recognise is filed as "the scanner saw nothing",
    // which is the conflation the outcome channel exists to remove.
    const spoiled: Metrics[] = [
      { ...passing, areaFraction: 0.01 },
      { ...passing, aspect: 99 },
      { ...passing, minAngle: 10 },
      { ...passing, convexity: 0.1 },
    ];
    for (const m of spoiled) {
      const r = firstHardReject(m, p);
      expect(r).not.toBeNull();
      expect(isRejectOutcome(r!)).toBe(true);
    }
  });
});

describe("outcomeForNoQuad — why did nothing come back", () => {
  it("reports a detector failure as error", () => {
    expect(outcomeForNoQuad({ rawCorners: null, error: "Scanner error: boom" })).toBe("error");
    // Error wins even when corners are present: the detector broke.
    expect(outcomeForNoQuad({ rawCorners: {}, error: "Scanner: bad" })).toBe("error");
  });

  it("reports corners that failed toQuad as malformed", () => {
    expect(outcomeForNoQuad({ rawCorners: { topLeft: { x: NaN, y: 0 } } })).toBe("malformed-corners");
  });

  /**
   * The load-bearing case. `classifyScanResult` stays deliberately silent on
   * scanic's default empty-frame message, so a bare table arrives here with no
   * error and no corners. Calling that an error would pin the viewfinder's
   * badge on permanently.
   */
  it("reports an honest empty frame as no-contour", () => {
    expect(outcomeForNoQuad({ rawCorners: null })).toBe("no-contour");
    expect(outcomeForNoQuad(classifyScanResult({ success: false, message: "No document detected" })))
      .toBe("no-contour");
  });

  it("never reports a no-quad state as a reject", () => {
    const states = [
      { rawCorners: null },
      { rawCorners: {} },
      { rawCorners: null, error: "x" },
    ];
    for (const s of states) expect(isRejectOutcome(outcomeForNoQuad(s))).toBe(false);
  });
});

describe("REJECT_OUTCOMES", () => {
  it("lists exactly the rejects, and every member carries the prefix", () => {
    expect([...REJECT_OUTCOMES].sort()).toEqual(
      ["rejected-angle", "rejected-area", "rejected-aspect", "rejected-convexity", "rejected-score"],
    );
    for (const o of REJECT_OUTCOMES) expect(o.startsWith("rejected-")).toBe(true);
  });

  it("recognises rejects and refuses everything else", () => {
    expect(isRejectOutcome("rejected-area")).toBe(true);
    expect(isRejectOutcome("no-contour")).toBe(false);
    expect(isRejectOutcome("accepted")).toBe(false);
    expect(isRejectOutcome("error")).toBe(false);
    expect(isRejectOutcome(undefined)).toBe(false);
  });
});
