import { useCallback, useEffect, useRef, useState } from "react";

export interface UseCameraOptions {
  /** Preferred capture width. Tops the constraint ladder. Default 3840. */
  width?: number;
  /** Preferred capture height. Tops the constraint ladder. Default 2160. */
  height?: number;
}

export interface UseCameraResult {
  /** Re-run acquisition from the first ladder rung. */
  retry: () => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  error: string | null;
  ready: boolean;
  /**
   * `track.getSettings()` for the acquired video track — the resolution the
   * device ACTUALLY granted, which is rarely the one requested. Null until a
   * stream is acquired. Re-read once frames start flowing, because some
   * drivers report 0×0 (or nothing at all) before the first frame.
   */
  settings: MediaTrackSettings | null;
}

/**
 * getUserMedia itself can hang: a driver that never resolves the promise throws
 * nothing, produces no stream, and leaves the ladder mid-rung forever — the
 * black-spinner-with-no-error failure. Only a timer catches it.
 *
 * Deliberately generous: the browser's permission prompt lives INSIDE the first
 * getUserMedia call and stays open for as long as the user takes to read it,
 * and rungs 1-2 can each burn seconds failing on a multi-lens Android device.
 * A tighter bound would abort a perfectly good permission grant.
 */
const READY_TIMEOUT_MS = 20_000;

/**
 * Last-resort readiness fallback (restored from b27a868): if neither
 * `loadedmetadata` nor `loadeddata` fires (autoplay blocked with deferred
 * metadata on some browsers) but frames ARE flowing, force ready so the
 * viewfinder can't latch on the black loading overlay forever (issue #4).
 */
const METADATA_FALLBACK_MS = 2_500;

/**
 * If frames still haven't arrived by this point the stream failed to start
 * (a camera the OS handed over but never started, a lens that never produces).
 * A non-producing track throws NOTHING, so this timer is the only thing that
 * catches it. Surface a retriable error instead of an infinite black spinner.
 */
const STUCK_TIMEOUT_MS = 7_000;

const STUCK_MESSAGE = "Camera did not start. Tap to retry.";

// The ladder's floor: the resolution that negotiates reliably on every device
// we have seen. Requests at or below this are not worth an `exact` rung.
const BASE_WIDTH = 1920;
const BASE_HEIGHT = 1080;

// Standard step-downs between the caller's request and the 1080p floor.
const EXACT_RUNGS: ReadonlyArray<readonly [number, number]> = [
  [3840, 2160],
  [2560, 1440],
];

const DEFAULT_WIDTH = 3840;
const DEFAULT_HEIGHT = 2160;

/**
 * Build the getUserMedia constraint ladder, highest resolution first.
 *
 * Why `exact` on the upper rungs, when `exact` is the thing that makes
 * getUserMedia reject: that rejection IS the mechanism. An `ideal`-only ladder
 * never reaches its lower rungs, because `ideal` never rejects on resolution —
 * getUserMedia resolves with whatever it felt like giving you and the loop
 * exits on the first rung every time. `exact` makes `OverconstrainedError`
 * fire, which is what advances the ladder to a resolution the device can
 * actually deliver.
 *
 * `facingMode` stays `ideal` on every rung: making it exact would fail outright
 * on a front-camera-only device instead of degrading to whatever camera exists.
 *
 * Rungs, for the default 3840×2160 request:
 *   1. exact 3840×2160 + environment
 *   2. exact 2560×1440 + environment
 *   3. ideal 1920×1080 + environment
 *   4. environment only
 *   5. any camera
 */
export function buildConstraintLadder(width?: number, height?: number): MediaStreamConstraints[] {
  const reqW = width && width > 0 ? Math.round(width) : DEFAULT_WIDTH;
  const reqH = height && height > 0 ? Math.round(height) : DEFAULT_HEIGHT;
  const basePixels = BASE_WIDTH * BASE_HEIGHT;

  const targets: Array<[number, number]> = [];
  const push = (w: number, h: number) => {
    // Nothing at or below the 1080p floor earns an `exact` rung — rung 3 already
    // covers it, and more so: `ideal` there can only do better, never worse.
    if (w * h <= basePixels) return;
    if (targets.some(([tw, th]) => tw === w && th === h)) return;
    targets.push([w, h]);
  };

  push(reqW, reqH);
  // Step down through the standard rungs that sit strictly BELOW the request.
  // A caller asking for 1080p gets no exact rungs at all; one asking for 8K gets
  // its own rung on top of 4K and 1440p.
  for (const [w, h] of EXACT_RUNGS) {
    if (w * h < reqW * reqH) push(w, h);
  }

  return [
    ...targets.map(([w, h]) => ({
      video: {
        facingMode: { ideal: "environment" },
        width: { exact: w },
        height: { exact: h },
      },
    })),
    {
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: BASE_WIDTH },
        height: { ideal: BASE_HEIGHT },
      },
    },
    { video: { facingMode: { ideal: "environment" } } },
    { video: true },
  ];
}

/**
 * Errors that no looser constraint can fix. Retrying these just makes the user
 * wait through four more rungs to read the same message.
 *
 * - `NotAllowedError` — permission denied. Every rung will be denied too.
 * - `NotFoundError` — no camera hardware. Same.
 * - `SecurityError` / `TypeError` — insecure context or a malformed request;
 *   both are our bug or the page's, not the device's.
 */
const TERMINAL_ERRORS = new Set(["NotAllowedError", "NotFoundError", "SecurityError", "TypeError"]);

export type LadderDecision = "advance" | "stop";

/**
 * Should the ladder try the next rung after this error?
 *
 * The two that MUST advance:
 * - `OverconstrainedError` — the exact resolution isn't supported. This is the
 *   designed exit from rungs 1-2.
 * - `NotReadableError` — "Couldn't start video source". The Galaxy S26 Ultra's
 *   actual failure (9dcd7b7): the physical camera the OS maps "environment"
 *   onto cannot initialize at the requested resolution. A looser rung gets a
 *   working camera; giving up here gets a black screen.
 *
 * `AbortError` is Firefox's spelling of the same "device failed to start"
 * condition, so it advances too. Anything unrecognized advances as well —
 * an unknown error name on rung 1 should not deny the user rung 5.
 */
export function ladderDecision(errorName: string | null | undefined): LadderDecision {
  return errorName && TERMINAL_ERRORS.has(errorName) ? "stop" : "advance";
}

/** Shape we actually read off a DOMException, without asserting `any`. */
export interface CameraErrorLike {
  name?: string;
  message?: string;
}

/** Turn a getUserMedia failure into something a human can act on. */
export function cameraErrorMessage(err: CameraErrorLike | null | undefined): string {
  switch (err?.name) {
    case "NotAllowedError":
      return "Camera permission denied. Please allow camera access and refresh.";
    case "NotFoundError":
      return "No camera found on this device.";
    case "NotReadableError":
    case "AbortError":
      return (
        "Camera is in use by another app, or the system refused to start it. " +
        "Close other camera apps, then close and reopen the scanner."
      );
    case "OverconstrainedError":
      return "This camera can't provide a usable video mode. Try another device.";
    default:
      return `Camera error: ${err?.message ?? "unknown"}`;
  }
}

function readSettings(stream: MediaStream): MediaTrackSettings | null {
  return stream.getVideoTracks()[0]?.getSettings() ?? null;
}

export function useCamera(opts?: UseCameraOptions): UseCameraResult {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [settings, setSettings] = useState<MediaTrackSettings | null>(null);
  /**
   * Bumped by `retry()` to re-run acquisition from rung 1. The error strings
   * promise "Tap to retry", so something has to make that true; a caller-side
   * `key` bump works but only if every call site remembers to wire one.
   */
  const [attempt, setAttempt] = useState(0);

  // Destructured to PRIMITIVES on purpose. Putting `opts` itself in the dep
  // array would restart getUserMedia on every render for the (entirely normal)
  // call site `useCamera({ width: 3840, height: 2160 })`, because that object
  // literal is a new identity each time. With rungs 1-2 taking seconds to fail,
  // that restart loop is not a wasted render — it is a permanent hang.
  const reqWidth = opts?.width;
  const reqHeight = opts?.height;

  // Effect A — acquire a stream by walking the constraint ladder.
  //
  // Binding to the <video> element happens in Effect B, not here, so a null
  // videoRef at resolve time is retried on the next render instead of being
  // silently dropped (the black-screen race, issue #4).
  useEffect(() => {
    let cancelled = false;
    let timedOut = false;
    let acquired: MediaStream | null = null;

    // Re-acquiring: clear the previous attempt's results here in the effect
    // BODY, never in its cleanup — a setState during unmount teardown is a
    // no-op at best. On first mount these are already the initial values and
    // React bails out of the re-render; on a deliberate re-acquire (retry, or a
    // requested-resolution change) the extra render is the point, because the
    // UI must stop showing the previous attempt's error and readiness.
    /* eslint-disable react-hooks/set-state-in-effect -- deliberate reset on
       re-acquire; see above. The alternative (a `key` bump at every call site)
       pushes correctness onto callers and was the source of the original
       black-screen race. */
    setStream(null);
    setReady(false);
    setError(null);
    setSettings(null);
    /* eslint-enable react-hooks/set-state-in-effect */

    const watchdog = setTimeout(() => {
      if (cancelled || acquired) return;
      timedOut = true;
      setError(STUCK_MESSAGE);
    }, READY_TIMEOUT_MS);

    async function start() {
      const ladder = buildConstraintLadder(reqWidth, reqHeight);
      let lastErr: CameraErrorLike | null = null;

      for (const constraints of ladder) {
        if (cancelled || timedOut) return;
        try {
          const s = await navigator.mediaDevices.getUserMedia(constraints);
          // Unmounted, or the watchdog already gave up, while this rung was in
          // flight. Stop the tracks NOW: an orphaned stream holds the camera
          // open and the next attempt gets NotReadableError from our own leak.
          if (cancelled || timedOut) {
            s.getTracks().forEach((t) => t.stop());
            return;
          }
          acquired = s;
          clearTimeout(watchdog);
          setSettings(readSettings(s));
          setStream(s);
          return;
        } catch (err) {
          lastErr = err as CameraErrorLike;
          if (ladderDecision(lastErr?.name) === "stop") break;
        }
      }

      // Every rung failed. Don't overwrite the watchdog's message with a stale
      // one, and don't touch state after unmount.
      if (cancelled || timedOut) return;
      clearTimeout(watchdog);
      setError(cameraErrorMessage(lastErr));
    }

    start();

    return () => {
      cancelled = true;
      clearTimeout(watchdog);
      // The one place acquired tracks are stopped: exactly once, on unmount or
      // re-acquisition. In-flight rungs stop themselves via the `cancelled`
      // check above.
      acquired?.getTracks().forEach((t) => t.stop());
      acquired = null;
    };
  }, [reqWidth, reqHeight, attempt]);

  // Effect B — bind the stream to the video element and track readiness.
  // Runs after every render where `stream` changed, so videoRef is reliably
  // attached. Handles the metadata race two ways: read readyState directly for
  // the case where metadata already loaded (the event would never fire again),
  // and listen for both loadedmetadata and loadeddata for the case where it
  // hasn't.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !stream) return;

    video.srcObject = stream;

    // `loadedmetadata` and `loadeddata` both fire on a healthy stream, and the
    // fallback timer can fire on top of them. Latch so we don't push a fresh
    // MediaTrackSettings object (new identity, new render) three times over.
    let settled = false;
    const markReady = () => {
      if (settled) return;
      settled = true;
      setReady(true);
      // Re-read now that frames are flowing: several Android drivers report a
      // placeholder (or nothing) from getSettings() before the first frame, so
      // the acquisition-time read can understate the granted resolution.
      setSettings(readSettings(stream));
    };

    if (video.readyState >= 1 /* HAVE_METADATA */) {
      markReady();
    }
    video.addEventListener("loadedmetadata", markReady);
    video.addEventListener("loadeddata", markReady);

    // autoPlay is advisory on mobile — start playback explicitly.
    video.play().catch(() => {});

    const readyTimer = setTimeout(() => {
      if (video.videoWidth > 0) markReady();
    }, METADATA_FALLBACK_MS);

    // Read videoWidth fresh at fire time (not stale `ready` state): if the
    // stream produced frames it's >0 and we stay silent; if it's still 0 the
    // camera never started, so show an actionable, retriable error.
    const stuckTimer = setTimeout(() => {
      if (video.videoWidth === 0) setError(STUCK_MESSAGE);
    }, STUCK_TIMEOUT_MS);

    return () => {
      clearTimeout(readyTimer);
      clearTimeout(stuckTimer);
      video.removeEventListener("loadedmetadata", markReady);
      video.removeEventListener("loadeddata", markReady);
      // Detach only if this effect's stream is still the one attached; the
      // tracks themselves are stopped by Effect A, which owns them.
      if (video.srcObject === stream) video.srcObject = null;
    };
  }, [stream]);

  /** Walk the constraint ladder again from the top. Safe to call any time. */
  const retry = useCallback(() => {
    setError(null);
    setReady(false);
    setAttempt((n) => n + 1);
  }, []);

  return { videoRef, error, ready, settings, retry };
}
