import { describe, it, expect } from "vitest";
import {
  CLASSICAL_DEFAULTS,
  classifyScanResult,
  firstHardReject,
  outcomeForNoQuad,
  mergeDetectionPasses,
  scanicDetectOptions,
  scanThrowMessage,
  toQuad,
  type Metrics,
} from "./classical-detector";
import { REJECT_OUTCOMES, isRejectOutcome } from "./detector";
import type { DetectionResult, Quad } from "./detector";

describe("mergeDetectionPasses — which pass gets reported", () => {
  const q: Quad = {
    topLeft: { x: 0, y: 0 },
    topRight: { x: 10, y: 0 },
    bottomRight: { x: 10, y: 20 },
    bottomLeft: { x: 0, y: 20 },
  };
  const res = (o: Partial<DetectionResult>): DetectionResult => ({
    corners: null, score: 0, candidates: [], timingMs: 1, outcome: "no-contour", ...o,
  });

  it("reports the configured pass when it accepted", () => {
    const out = mergeDetectionPasses(res({ outcome: "rejected-area", candidates: [{ quad: q, score: 0.2 }] }), res({ corners: q, outcome: "accepted" }));
    expect(out.outcome).toBe("accepted");
    expect(out.corners).toBe(q);
  });

  it("reports the CONFIGURED pass's reject reason, not the raw pass's", () => {
    // The Lab tallies `outcome` to tell a too-tight threshold from a blind
    // scanner. Attributing the raw pass's reject to a preprocessed panel would
    // answer a question about the other arm.
    const out = mergeDetectionPasses(
      res({ outcome: "rejected-area", candidates: [{ quad: q, score: 0.2 }] }),
      res({ outcome: "rejected-angle", candidates: [{ quad: q, score: 0.3 }] }),
    );
    expect(out.outcome).toBe("rejected-angle");
  });

  it("keeps the raw pass's evidence when the configured pass saw nothing at all", () => {
    // A rejected quad is the only proof scanic found SOMETHING here. Throwing it
    // away would make a too-tight gate look identical to a blind frame.
    const out = mergeDetectionPasses(
      res({ outcome: "rejected-area", candidates: [{ quad: q, score: 0.2 }] }),
      res({ outcome: "no-contour" }),
    );
    expect(out.outcome).toBe("rejected-area");
    expect(out.candidates).toHaveLength(1);
  });

  it("carries a detector FAILURE across even when the other pass merely came back empty", () => {
    // `error` is the one signal meaning "the detector is broken" rather than
    // "this frame is empty". If the raw pass throws and the preprocessed pass
    // finds nothing, the viewfinder badge must still light.
    const out = mergeDetectionPasses(
      res({ outcome: "error", error: "Scanner error: wasm boom" }),
      res({ outcome: "no-contour" }),
    );
    expect(out.error).toMatch(/wasm boom/);
    expect(out.outcome).toBe("error");
  });

  it("carries a failure from the configured pass too", () => {
    const out = mergeDetectionPasses(
      res({ outcome: "no-contour" }),
      res({ outcome: "error", error: "Scanner error: later boom" }),
    );
    expect(out.error).toMatch(/later boom/);
    expect(out.outcome).toBe("error");
  });

  it("does NOT invent an error for two honestly empty frames", () => {
    const out = mergeDetectionPasses(res({ outcome: "no-contour" }), res({ outcome: "no-contour" }));
    expect(out.error).toBeUndefined();
    expect(out.outcome).toBe("no-contour");
  });

  it("keeps a usable quad's own outcome even when a pass errored", () => {
    const out = mergeDetectionPasses(
      res({ outcome: "error", error: "Scanner error: boom" }),
      res({ corners: q, outcome: "accepted" }),
    );
    expect(out.outcome).toBe("accepted");
    expect(out.error).toMatch(/boom/);
  });
});

describe("scanicDetectOptions — scanic's aspect cap follows ours", () => {
  it("passes our maxAspect through, so scanic 1.6's 8:1 default never binds first", () => {
    // A 10:1 restaurant slip is inside our 12:1 gate. Left at scanic's default
    // of 8, scanic would mark it invalid and rank any valid rectangle nearby
    // above it — the slip would never reach our gates to be accepted.
    expect(scanicDetectOptions(CLASSICAL_DEFAULTS)).toEqual({ mode: "detect", maxDocumentAspectRatio: 12 });
    expect(scanicDetectOptions({ maxAspect: 20 }).maxDocumentAspectRatio).toBe(20);
  });
});

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
    spanFraction: 0.7,
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

  describe("the long-document exemption from minAreaFraction", () => {
    /**
     * The 2026-09-12 capture that would not lock: a till slip covering 11.9% of
     * the frame against a 12% floor, while spanning 56% of its height. Area
     * alone cannot see the difference between that and a scrap of noise, so the
     * exemption asks for length as well — and all three conditions together, or
     * it would readmit exactly the small junk the floor exists to reject.
     */
    const slip: Metrics = { ...passing, areaFraction: 0.119, spanFraction: 0.56, aspect: 4.4 };

    it("accepts a long thin slip that the area floor alone would reject", () => {
      expect(firstHardReject({ ...passing, areaFraction: 0.119 }, p)).toBe("rejected-area");
      expect(firstHardReject(slip, p)).toBeNull();
    });

    it("still rejects it when it is not long enough across the frame", () => {
      expect(firstHardReject({ ...slip, spanFraction: p.minSpanFraction - 0.01 }, p)).toBe("rejected-area");
    });

    it("still rejects it when it is not thin enough to be a receipt", () => {
      expect(firstHardReject({ ...slip, aspect: p.minSpanAspect - 0.1 }, p)).toBe("rejected-area");
    });

    it("REFUSES the long thin strip a table edge produces", () => {
      // The shape an adversarial review found at the original floor: in an
      // 800x450 detection frame, a 500x45 strip is 6% of the area, spans 64% of
      // the width, and clears convexity (1.0) and the 90-degree corner band
      // effortlessly. A table edge, a keyboard row, a strip light. It does not
      // move, so the smoother would lock onto it, the badge would go green, and
      // the "shoot anyway" escape hatch would never appear — a silent bad
      // capture, not a visible one. Both the aspect ceiling and the area floor
      // refuse it independently.
      const strip: Metrics = { ...passing, areaFraction: 0.06, spanFraction: 0.64, aspect: 12 };
      expect(firstHardReject(strip, p)).toBe("rejected-area");
      expect(firstHardReject({ ...strip, aspect: 8 }, p)).toBe("rejected-area");
      expect(firstHardReject({ ...strip, areaFraction: 0.1 }, p)).toBe("rejected-area");
    });

    it("rejects anything longer than maxSpanAspect even at a healthy area", () => {
      expect(firstHardReject({ ...slip, aspect: p.maxSpanAspect + 0.1 }, p)).toBe("rejected-area");
    });

    it("still rejects it when it has all but vanished", () => {
      expect(firstHardReject({ ...slip, areaFraction: p.minSpanAreaFraction - 0.001 }, p)).toBe("rejected-area");
    });

    it("never exempts a quad from the UPPER bound — a near-full-frame quad still fails", () => {
      // maxAreaFraction guards against locking onto the whole photo. Length must
      // not buy a way past it.
      expect(firstHardReject({ ...slip, areaFraction: 0.99, spanFraction: 0.99 }, p)).toBe("rejected-area");
    });

    it("rejects a quad whose span is not a number", () => {
      expect(firstHardReject({ ...passing, spanFraction: NaN }, p)).toBe("rejected-area");
    });
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
