import { describe, it, expect } from "vitest";
import { DETECTION_MAX_EDGE, detectionSizeFor } from "./detection-size";

/**
 * The no-upscale clamp is the reason this function exists rather than an inline
 * `Math.round(videoWidth * k)`.
 *
 * Until 2026-09-05 the live loop sized detection as a FRACTION of the video
 * (`videoWidth * 0.4`), which is monotonically a downscale and therefore safe by
 * construction. A fixed 800px target is not: on a small source it would happily
 * invent pixels. These tests are what keep the replacement honest.
 */
describe("detectionSizeFor", () => {
  it("caps a 4K landscape frame at the target edge", () => {
    const s = detectionSizeFor(3840, 2160, 800);
    expect(s.w).toBe(800);
    expect(s.h).toBe(450);
    expect(s.scale).toBeCloseTo(800 / 3840, 10);
  });

  // Android routinely hands back a portrait-oriented track. The LONGEST edge is
  // capped either way; which axis that is must not matter.
  it("caps a 4K portrait frame on its longest edge, not its width", () => {
    const s = detectionSizeFor(2160, 3840, 800);
    expect(s.h).toBe(800);
    expect(s.w).toBe(450);
    expect(s.scale).toBeCloseTo(800 / 3840, 10);
  });

  it("handles a square frame", () => {
    const s = detectionSizeFor(1000, 1000, 800);
    expect(s).toEqual({ w: 800, h: 800, scale: 0.8 });
  });

  it("preserves aspect ratio", () => {
    const s = detectionSizeFor(3840, 2160, 800);
    expect(s.w / s.h).toBeCloseTo(3840 / 2160, 2);
  });

  // THE regression this file exists for.
  it("NEVER upscales a source already within the target", () => {
    const small = detectionSizeFor(640, 480, 800);
    expect(small).toEqual({ w: 640, h: 480, scale: 1 });

    const tiny = detectionSizeFor(100, 50, 800);
    expect(tiny).toEqual({ w: 100, h: 50, scale: 1 });
  });

  it("leaves a frame exactly at the target untouched, scale exactly 1", () => {
    const s = detectionSizeFor(800, 600, 800);
    expect(s).toEqual({ w: 800, h: 600, scale: 1 });
    // Exactly 1, not 0.9999...: callers branch on `scale === 1` to skip a copy.
    expect(s.scale).toBe(1);
  });

  it("returns scale 1 for a zero-size source rather than a fabricated size", () => {
    // videoWidth is 0 between camera-ready and the first decoded frame.
    expect(detectionSizeFor(0, 0, 800)).toEqual({ w: 0, h: 0, scale: 1 });
    expect(detectionSizeFor(1920, 0, 800).scale).not.toBeNaN();
  });

  it("returns scale 1 for a non-positive or non-finite maxEdge", () => {
    expect(detectionSizeFor(3840, 2160, 0)).toEqual({ w: 3840, h: 2160, scale: 1 });
    expect(detectionSizeFor(3840, 2160, -100)).toEqual({ w: 3840, h: 2160, scale: 1 });
    expect(detectionSizeFor(3840, 2160, NaN)).toEqual({ w: 3840, h: 2160, scale: 1 });
  });

  it("never returns a zero dimension for an extreme aspect ratio", () => {
    // A 1:40 sliver would round its short edge to 0 without the max(1, ...).
    const s = detectionSizeFor(4000, 100, 800);
    expect(s.w).toBe(800);
    expect(s.h).toBeGreaterThanOrEqual(1);
  });

  it("defaults to DETECTION_MAX_EDGE", () => {
    expect(detectionSizeFor(3840, 2160)).toEqual(detectionSizeFor(3840, 2160, DETECTION_MAX_EDGE));
  });

  /**
   * The mapping the viewfinder actually performs: corners detected in the
   * downscaled frame are multiplied by `1 / scale` to land in video space.
   */
  it("round-trips a coordinate back to video space through `scale`", () => {
    const s = detectionSizeFor(3840, 2160, 800);
    const xInDetection = s.w; // the far edge
    expect(xInDetection / s.scale).toBeCloseTo(3840, 6);
  });
});
