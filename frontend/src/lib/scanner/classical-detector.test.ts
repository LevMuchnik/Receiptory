import { describe, it, expect } from "vitest";
import { classifyScanResult, scanThrowMessage, toQuad } from "./classical-detector";

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
