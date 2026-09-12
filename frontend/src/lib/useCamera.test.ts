import { describe, it, expect } from "vitest";
import { buildConstraintLadder, ladderDecision, cameraErrorMessage } from "./useCamera";

/**
 * Only the ladder's PURE decision logic is covered here: which rungs exist, in
 * what order, which errors advance to the next rung, and what the user is told
 * when everything fails.
 *
 * The hook itself is deliberately untested. `vitest.config.ts` runs in node with
 * no jsdom, and `useCamera` is a React hook that binds `navigator.mediaDevices`
 * to a live <video> element — covering it means jsdom plus a fake MediaStream
 * plus fake media events, i.e. testing the mocks. The risky part is the ladder,
 * the risky part is pure, and the risky part is what's below.
 */

/** Narrow the rung's constraint union down to something readable in a test. */
function videoConstraints(c: MediaStreamConstraints): MediaTrackConstraints | boolean {
  return c.video as MediaTrackConstraints | boolean;
}

function exactSize(c: MediaStreamConstraints): [number, number] | null {
  const v = videoConstraints(c);
  if (typeof v === "boolean") return null;
  const w = v.width as ConstrainULongRange | undefined;
  const h = v.height as ConstrainULongRange | undefined;
  if (w?.exact === undefined || h?.exact === undefined) return null;
  return [w.exact as number, h.exact as number];
}

describe("buildConstraintLadder", () => {
  it("defaults to the 4K-first five-rung ladder", () => {
    const ladder = buildConstraintLadder();
    expect(ladder).toHaveLength(5);
    expect(exactSize(ladder[0])).toEqual([3840, 2160]);
    expect(exactSize(ladder[1])).toEqual([2560, 1440]);
  });

  it("uses ideal (never exact) on the 1080p rung so it cannot over-constrain", () => {
    const rung = videoConstraints(buildConstraintLadder()[2]) as MediaTrackConstraints;
    expect(exactSize(buildConstraintLadder()[2])).toBeNull();
    expect(rung.width).toEqual({ ideal: 1920 });
    expect(rung.height).toEqual({ ideal: 1080 });
    expect(rung.facingMode).toEqual({ ideal: "environment" });
  });

  it("ends with environment-only and then any camera", () => {
    const ladder = buildConstraintLadder();
    const envOnly = videoConstraints(ladder[3]) as MediaTrackConstraints;
    expect(envOnly).toEqual({ facingMode: { ideal: "environment" } });
    expect(videoConstraints(ladder[4])).toBe(true);
  });

  it("keeps facingMode ideal on the exact rungs, so a front-only device still degrades", () => {
    for (const rung of buildConstraintLadder()) {
      const v = videoConstraints(rung);
      if (typeof v === "boolean") continue;
      if (v.facingMode !== undefined) expect(v.facingMode).toEqual({ ideal: "environment" });
    }
  });

  it("descends monotonically in pixel count", () => {
    const sizes = buildConstraintLadder(7680, 4320)
      .map(exactSize)
      .filter((s): s is [number, number] => s !== null);
    expect(sizes).toEqual([
      [7680, 4320],
      [3840, 2160],
      [2560, 1440],
    ]);
  });

  it("gives a 1080p request no exact rungs at all — rung 3 already covers it", () => {
    const ladder = buildConstraintLadder(1920, 1080);
    expect(ladder).toHaveLength(3);
    expect(ladder.map(exactSize)).toEqual([null, null, null]);
  });

  it("treats a sub-1080p request the same way rather than laddering downward", () => {
    expect(buildConstraintLadder(1280, 720)).toHaveLength(3);
  });

  it("does not duplicate a requested size that is already a standard rung", () => {
    const sizes = buildConstraintLadder(2560, 1440)
      .map(exactSize)
      .filter((s): s is [number, number] => s !== null);
    expect(sizes).toEqual([[2560, 1440]]);
  });

  it("falls back to the default ladder for nonsense dimensions", () => {
    expect(buildConstraintLadder(0, 0)).toEqual(buildConstraintLadder());
    expect(buildConstraintLadder(-1, -1)).toEqual(buildConstraintLadder());
    expect(buildConstraintLadder(undefined, 2160)).toEqual(buildConstraintLadder());
  });
});

describe("ladderDecision", () => {
  it("advances on OverconstrainedError — the designed exit from the exact rungs", () => {
    expect(ladderDecision("OverconstrainedError")).toBe("advance");
  });

  it("advances on NotReadableError — the Galaxy S26 Ultra's actual failure", () => {
    expect(ladderDecision("NotReadableError")).toBe("advance");
  });

  it("advances on AbortError, Firefox's spelling of the same start failure", () => {
    expect(ladderDecision("AbortError")).toBe("advance");
  });

  it("stops on NotAllowedError — no rung survives a denied permission", () => {
    expect(ladderDecision("NotAllowedError")).toBe("stop");
  });

  it("stops on NotFoundError, SecurityError and TypeError", () => {
    expect(ladderDecision("NotFoundError")).toBe("stop");
    expect(ladderDecision("SecurityError")).toBe("stop");
    expect(ladderDecision("TypeError")).toBe("stop");
  });

  it("advances on an unrecognized or missing name rather than denying the last rung", () => {
    expect(ladderDecision("SomeVendorError")).toBe("advance");
    expect(ladderDecision(undefined)).toBe("advance");
    expect(ladderDecision(null)).toBe("advance");
    expect(ladderDecision("")).toBe("advance");
  });
});

describe("cameraErrorMessage", () => {
  it("tells the user how to fix a denied permission", () => {
    expect(cameraErrorMessage({ name: "NotAllowedError" })).toMatch(/permission/i);
  });

  it("names the two real causes of NotReadableError", () => {
    const msg = cameraErrorMessage({ name: "NotReadableError" });
    expect(msg).toMatch(/in use by another app/i);
    expect(msg).toMatch(/reopen the scanner/i);
  });

  it("gives AbortError the same actionable text", () => {
    expect(cameraErrorMessage({ name: "AbortError" })).toBe(cameraErrorMessage({ name: "NotReadableError" }));
  });

  it("reports no camera plainly", () => {
    expect(cameraErrorMessage({ name: "NotFoundError" })).toMatch(/no camera/i);
  });

  it("falls back to the raw message, and says so when there isn't one", () => {
    expect(cameraErrorMessage({ name: "WeirdError", message: "boom" })).toBe("Camera error: boom");
    expect(cameraErrorMessage(null)).toBe("Camera error: unknown");
    expect(cameraErrorMessage(undefined)).toBe("Camera error: unknown");
  });
});
