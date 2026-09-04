import { describe, it, expect, vi, beforeEach } from "vitest";
import { MLDetector, ML_DEFAULTS } from "./ml-detector";

/**
 * The detector ERROR-CHANNEL suite (step 1.5).
 *
 * `DetectionResult.error` exists to separate two states that used to look
 * identical from the outside:
 *
 *   corners: null, no error  -> the detector ran and saw an empty table
 *   corners: null, error set -> the detector could not run at all
 *
 * The viewfinder renders them differently ("Position document" vs "Detector
 * error: {msg}") and `ScannerLabPage.runEval` EXCLUDES errored frames from the
 * IoU median rather than scoring them 0 — which is the whole point, because a
 * wrong model URL scoring 0.00 across 31 frames reads as "this model is bad"
 * instead of "this eval never ran".
 *
 * Only the pre-inference failure paths are covered here. Everything past
 * `ensureSession` calls `letterbox()`, which needs a canvas; those paths are
 * left to the on-device Lab, deliberately, per vitest.config.ts.
 */

/** Stand-in for an ImageData. Never read on any path exercised below. */
const stubImage = { width: 640, height: 480, data: new Uint8ClampedArray(4) } as unknown as ImageData;

const createMock = vi.fn();
vi.mock("onnxruntime-web", () => ({
  InferenceSession: { create: (...args: unknown[]) => createMock(...args) },
  Tensor: class {},
}));

beforeEach(() => {
  createMock.mockReset();
  // The load paths log through console.warn by design; keep the suite output
  // readable without hiding a genuine unexpected warning elsewhere.
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("getDefaultParams", () => {
  it("hands back a COPY, so the Lab's editable fields cannot mutate the defaults", () => {
    const d = new MLDetector();
    const p = d.getDefaultParams();
    p.modelUrl = "/models/mutated.onnx";
    expect(ML_DEFAULTS.modelUrl).toBe("");
    expect(d.getDefaultParams().modelUrl).toBe("");
  });
});

describe("detect — misconfiguration is an ERROR, not an empty table", () => {
  it("reports an error when no model URL is configured", async () => {
    // ML_DEFAULTS.modelUrl is "", so this is the state the detector is in the
    // moment someone switches the Lab to the ML arm without picking a model.
    // Returning a silent `corners: null` here would make a misconfiguration
    // indistinguishable from a bare table and quietly score 0 in runEval.
    const r = await new MLDetector().detect(stubImage, {});
    expect(r.corners).toBeNull();
    expect(r.error).toBe("No ML model configured");
    expect(r.score).toBe(0);
    expect(r.candidates).toEqual([]);
  });

  it("does not touch the image at all on the unconfigured path", async () => {
    // Cheap proof that this branch returns before letterbox(): a null image
    // would throw the moment anything read .width.
    const r = await new MLDetector().detect(null as unknown as ImageData, { modelUrl: "" });
    expect(r.error).toBe("No ML model configured");
  });
});

describe("detect — model load failure", () => {
  it("surfaces the load failure in `error` rather than reporting a miss", async () => {
    createMock.mockRejectedValue(new Error("404 fetching model"));
    const r = await new MLDetector().detect(stubImage, { modelUrl: "/models/missing.onnx" });
    expect(r.corners).toBeNull();
    expect(r.error).toMatch(/^ML model load failed: /);
    expect(r.error).toContain("404 fetching model");
  });

  it("stringifies a non-Error rejection instead of emitting 'undefined'", async () => {
    // onnxruntime-web's wasm layer can reject with a bare string.
    createMock.mockRejectedValue("abort(no wasm)");
    const r = await new MLDetector().detect(stubImage, { modelUrl: "/models/broken.onnx" });
    expect(r.error).toBe("ML model load failed: abort(no wasm)");
  });

  it("retries the load on the next detect rather than latching the failure", async () => {
    // Unlike Scanic's init, MLDetector clears `loading` in a finally block and
    // leaves `session` null, so a transient network failure must not become
    // permanent for the life of the page.
    const d = new MLDetector();
    createMock.mockRejectedValueOnce(new Error("transient"));
    const first = await d.detect(stubImage, { modelUrl: "/models/m.onnx" });
    expect(first.error).toContain("transient");

    createMock.mockRejectedValueOnce(new Error("second attempt"));
    const second = await d.detect(stubImage, { modelUrl: "/models/m.onnx" });
    // A second create() call is the proof the first failure did not latch.
    expect(createMock).toHaveBeenCalledTimes(2);
    expect(second.error).toContain("second attempt");
  });
});
