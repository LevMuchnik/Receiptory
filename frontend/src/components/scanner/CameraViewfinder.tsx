import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCamera } from "@/lib/useCamera";
import type { Detector, Quad } from "@/lib/scanner/detector";
import { TemporalSmoother } from "@/lib/scanner/smoother";
import { detectionSizeFor } from "@/lib/scanner/detection-size";

interface CameraViewfinderProps {
  detector: Detector;
  detectorParams: any;
  /**
   * @param imageData    Full video-resolution frame grabbed from the <video>.
   * @param emaCorners   Smoothed quad in DETECTION space (may be null).
   * @param detectionScale  The ratio actually used this frame
   *                        (detection dimension / video dimension), so the
   *                        consumer can convert `emaCorners` into video pixels.
   *                        Always the measured value -- never a constant. A
   *                        duplicated magic 0.4 is how the original coordinate
   *                        bug happened.
   */
  onCapture: (imageData: ImageData, emaCorners: Quad | null, detectionScale: number) => void;
}

/*
 * Detection is sized by `detectionSizeFor(videoW, videoH, DETECTION_MAX_EDGE)`.
 *
 * It used to be a FRACTION of the video — `videoWidth * 0.4` — which was tuned
 * when the preview was 1080p: 0.4 x 1920 = 768px, matching the ~800px contract
 * in `detector.ts`. When the camera ladder was raised to 3840x2160 that fraction
 * silently became 1536px, four times the pixels every ClassicalParams default
 * was tuned against, and every one above 800 is discarded by scanic anyway —
 * but only after `preprocess()` has paid five per-pixel JS passes on it.
 *
 * Verified on-device 2026-09-05: with the request capped at 1080p the box is
 * measurably more stable than at 4K, same receipt, same light. A fixed edge
 * gives the scanner that stability while `handleCapture` keeps grabbing the full
 * video frame, so the final extract still gets every 4K pixel.
 */

/**
 * The control arm for the "did 4K break detection?" experiment (E2).
 *
 * `useCamera()` with no argument requests 3840x2160 and walks the ladder down.
 * This forces the ladder to start at 1080p instead, which is where the detector
 * parameters were originally tuned. The toggle exists because the two arms have
 * to be compared against the SAME receipt under the SAME light — two builds
 * shot minutes apart is not a control, and the on-device verdict is the only
 * verdict this subsystem accepts.
 *
 * On many Android devices the 4K rung also selects a different physical lens,
 * with a narrower field of view and a longer minimum focus distance. A lens that
 * cannot focus at receipt distance produces frames with no contour to find,
 * which looks exactly like a broken detector. The `cam` line in the diagnostics
 * strip reports `deviceId`, so a lens swap between the two arms is visible
 * rather than inferred.
 *
 * Module scope, not an inline literal: `useCamera` destructures to primitives
 * precisely so a fresh object identity per render cannot restart getUserMedia,
 * but there is no reason to lean on that.
 */
const LADDER_CONTROL_1080P = { width: 1920, height: 1080 };

/** What the detection loop last actually ran on. Diagnostics only. */
interface Diagnostics {
  videoW: number;
  videoH: number;
  detW: number;
  detH: number;
}
/**
 * Floor on the gap between detections. `requestVideoFrameCallback` fires once
 * per video frame (~30-60Hz); the re-entrancy guard alone would run a ~50ms
 * main-thread detection roughly 5x more often than the old `rAF % 5` cadence
 * and make the preview worse, not better.
 *
 * COUPLED CONSTANT — this plus `Detector.detect()` latency is one detection
 * cycle, and `TemporalSmoother`'s `staleMs` (smoother.ts, 250ms) must exceed it
 * or the lock is released between results and the box goes jumpy. Detection is
 * capped at `DETECTION_MAX_EDGE` to hold detect latency down. Raise one, check
 * the other.
 */
const MIN_DETECT_INTERVAL_MS = 80;

export default function CameraViewfinder({
  detector,
  detectorParams,
  onCapture,
}: CameraViewfinderProps) {
  /**
   * E2's control switch. Defaults to true, i.e. the shipped 4K behaviour —
   * the experiment must not change what the scanner does by default.
   */
  const [capture4k, setCapture4k] = useState(true);
  const { videoRef, error, ready, retry, settings } = useCamera(
    capture4k ? undefined : LADDER_CONTROL_1080P,
  );
  /**
   * Written only when the detection space actually changes, which is the same
   * moment the offscreen canvas is resized — once per camera acquisition or
   * orientation change, never per frame.
   */
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const overlayGroupRef = useRef<SVGGElement>(null);
  const polygonRef = useRef<SVGPolygonElement>(null);
  const cornerRefs = useRef<(SVGCircleElement | null)[]>([null, null, null, null]);
  /**
   * Detection-space corners. Deliberately a ref, not state: the overlay is
   * updated imperatively so a detection never costs a React render.
   */
  const cornersRef = useRef<Quad | null>(null);
  /**
   * Seeded at 1 (identity), not at a guessed ratio: until the first detection
   * has run there is no video size to derive one from, and a stale guess used
   * by a capture that lands first is a silently wrong crop. `handleCapture`
   * only ever divides by this alongside corners that the same detection
   * produced, so identity is the honest starting value.
   */
  const detectionScaleRef = useRef(1);
  const [documentDetected, setDocumentDetected] = useState(false);
  const [detectorError, setDetectorError] = useState<string | null>(null);
  const detecting = useRef(false);
  const smoother = useMemo(() => new TemporalSmoother(), []);

  useEffect(() => {
    if (!ready) return;
    const video = videoRef.current;
    if (!video) return;

    // Created and configured ONCE. Only `width`/`height` are touched later, and
    // only when the video resolution actually changes (assigning them clears
    // the canvas, so doing it per detection was pure waste).
    const offscreen = document.createElement("canvas");
    const ctx = offscreen.getContext("2d", { willReadFrequently: true });

    // Detection-space dimensions of the most recent detection. The overlay
    // viewBox must match these exactly, because `cornersRef` lives in them.
    let detW = 0;
    let detH = 0;
    let lastViewBox = "";
    let lastPoints = "";
    let lastDetectAt = 0;
    let handle = 0;
    let cancelled = false;

    const useRvfc = typeof video.requestVideoFrameCallback === "function";

    function drawOverlay() {
      const svg = svgRef.current;
      const group = overlayGroupRef.current;
      const polygon = polygonRef.current;
      if (!svg || !group || !polygon) return;

      const corners = cornersRef.current;
      if (!corners || detW === 0 || detH === 0) {
        if (lastPoints !== "") {
          lastPoints = "";
          polygon.setAttribute("points", "");
          group.setAttribute("display", "none");
        }
        return;
      }

      // The browser owns the cover math via preserveAspectRatio="...slice";
      // all we do is publish the coordinate system the points are expressed in.
      const viewBox = `0 0 ${detW} ${detH}`;
      if (viewBox !== lastViewBox) {
        lastViewBox = viewBox;
        svg.setAttribute("viewBox", viewBox);
        // r is in viewBox units, so it has to track the detection resolution or
        // the handles change apparent size whenever that resolution changes.
        const r = Math.max(3, detH * 0.01).toFixed(2);
        for (const circle of cornerRefs.current) circle?.setAttribute("r", r);
      }

      const pts = [corners.topLeft, corners.topRight, corners.bottomRight, corners.bottomLeft];
      const points = pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
      if (points !== lastPoints) {
        lastPoints = points;
        polygon.setAttribute("points", points);
        for (let i = 0; i < pts.length; i++) {
          const circle = cornerRefs.current[i];
          if (!circle) continue;
          circle.setAttribute("cx", pts[i].x.toFixed(1));
          circle.setAttribute("cy", pts[i].y.toFixed(1));
        }
        group.setAttribute("display", "inline");
      }
    }

    function schedule() {
      if (cancelled || !video) return;
      handle = useRvfc ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
    }

    function tick() {
      if (cancelled || !video) return;
      const now = performance.now();

      // The smoother's expiry is authoritative: read it every frame so a
      // detector that stops calling push() (because it threw) cannot leave a
      // ghost quad frozen on screen.
      if (cornersRef.current && !smoother.getEMA(now)) {
        cornersRef.current = null;
        setDocumentDetected(false);
      }

      if (
        ctx &&
        video.videoWidth > 0 &&
        !detecting.current &&
        now - lastDetectAt >= MIN_DETECT_INTERVAL_MS
      ) {
        lastDetectAt = now;
        detecting.current = true;

        const size = detectionSizeFor(video.videoWidth, video.videoHeight);
        const { w, h } = size;
        if (offscreen.width !== w || offscreen.height !== h) {
          offscreen.width = w;
          offscreen.height = h;
          // The detection space just changed (device rotation, or Android
          // handing back a differently-oriented track). Everything the smoother
          // holds is expressed in the OLD space, and blendQuads would average
          // across two coordinate systems into a quad that belongs to neither.
          // Drop the lock and re-acquire in the new space.
          smoother.reset();
          cornersRef.current = null;
          setDocumentDetected(false);
          // Same trigger, so this costs one render per resolution change, not
          // one per detection.
          setDiagnostics({ videoW: video.videoWidth, videoH: video.videoHeight, detW: w, detH: h });
        }
        detW = w;
        detH = h;
        // The ratio `detectionSizeFor` actually applied, not one re-derived from
        // `w / video.videoWidth`. Those differ by up to half a pixel of rounding
        // and it is the same one-ratio-two-derivations shape that produced the
        // original overlay bug. Still never a constant.
        detectionScaleRef.current = size.scale;

        ctx.drawImage(video, 0, 0, w, h);
        const imageData = ctx.getImageData(0, 0, w, h);
        const diag = Math.hypot(w, h);

        detector
          .detect(imageData, detectorParams)
          .then((result) => {
            if (cancelled) return;
            smoother.push(result, diag);
            const ema = smoother.getEMA();
            cornersRef.current = ema;
            setDocumentDetected(!!ema);
            setDetectorError(result.error ?? null);
          })
          .catch((err: unknown) => {
            if (cancelled) return;
            cornersRef.current = null;
            setDocumentDetected(false);
            setDetectorError(err instanceof Error ? err.message : "detection failed");
          })
          .finally(() => {
            detecting.current = false;
          });
      }

      drawOverlay();
      schedule();
    }

    schedule();
    return () => {
      cancelled = true;
      if (useRvfc) video.cancelVideoFrameCallback(handle);
      else cancelAnimationFrame(handle);
    };
  }, [ready, videoRef, detector, detectorParams, smoother]);

  const handleCapture = useCallback(() => {
    const video = videoRef.current;
    // videoWidth can still be 0 in the window between `ready` and the first
    // decoded frame; getImageData(0,0,0,0) throws IndexSizeError out of the
    // onClick and the shutter appears to do nothing.
    if (!video || !ready || !(video.videoWidth > 0) || !(video.videoHeight > 0)) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    onCapture(imageData, cornersRef.current, detectionScaleRef.current);
  }, [videoRef, ready, onCapture]);

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center bg-black p-6">
        <div className="text-center text-white space-y-4">
          <span className="material-symbols-outlined text-4xl text-[#ffdad6]">videocam_off</span>
          <p className="text-sm">{error}</p>
          {/*
            The error strings say "Tap to retry" — so this has to exist, or the
            message is a lie and the user is stranded on a black screen. retry()
            walks the constraint ladder again from rung 1.
          */}
          <button
            onClick={retry}
            className="px-6 py-3 rounded-xl bg-white/10 font-bold text-sm active:bg-white/20"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  const badgeText = detectorError
    ? `Detector error: ${detectorError}`
    : documentDetected
      ? "Document detected"
      : "Position document in frame";
  const badgeClass = detectorError
    ? "bg-[#ba1a1a]/85 text-white"
    : documentDetected
      ? "bg-[#006d37]/80 text-white"
      : "bg-black/50 text-white/70";

  return (
    <div className="flex-1 relative bg-black flex items-center justify-center overflow-hidden">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="absolute inset-0 w-full h-full object-cover"
      />
      {/*
        "xMidYMid slice" IS object-cover: the browser applies the same uniform
        scale-and-crop it applies to the <video> above, so the overlay cannot
        drift out of sync with the CSS. Never compute cover-scale in JS.
        viewBox is in DETECTION space and is set imperatively, since the
        detection dimensions are unknown until videoWidth > 0.
      */}
      <svg
        ref={svgRef}
        viewBox="0 0 1 1"
        preserveAspectRatio="xMidYMid slice"
        className="absolute inset-0 w-full h-full pointer-events-none"
      >
        <g ref={overlayGroupRef} display="none">
          <polygon
            ref={polygonRef}
            points=""
            fill="rgba(0, 109, 55, 0.15)"
            stroke="#006d37"
            strokeWidth={3}
            vectorEffect="non-scaling-stroke"
          />
          {[0, 1, 2, 3].map((i) => (
            <circle
              key={i}
              ref={(el) => {
                cornerRefs.current[i] = el;
              }}
              r={3}
              fill="#006d37"
              stroke="white"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </g>
      </svg>

      {/*
        Diagnostics strip (T1/T2). Reports what the camera actually granted
        versus what was asked for, and what the detector actually ran on.
        Every scanner verdict before 2026-09-05 was given without any of these
        numbers visible, which is how "detection is broken" and "the ladder
        negotiated a different lens" stayed indistinguishable for months.
      */}
      <div className="absolute top-2 left-2 right-2 flex items-start gap-2 pointer-events-none">
        <button
          type="button"
          onClick={() => setCapture4k((v) => !v)}
          className="pointer-events-auto shrink-0 px-2 py-1 rounded-md bg-black/60 text-white text-[10px] font-bold tracking-wider backdrop-blur-sm active:bg-black/80"
        >
          {capture4k ? "REQ 4K" : "REQ 1080p"}
        </button>
        <div className="flex-1 min-w-0 px-2 py-1 rounded-md bg-black/50 text-white/70 text-[10px] font-mono leading-tight backdrop-blur-sm">
          {/*
            `granted` is track.getSettings(); `video` is the decoded frame size.
            They disagree whenever Android hands back a landscape-oriented track
            under a portrait UI, and detection follows `video`, not `granted`.
          */}
          <div>
            granted {settings?.width ?? "?"}x{settings?.height ?? "?"}
            {settings?.frameRate ? ` @${Math.round(settings.frameRate)}fps` : ""}
          </div>
          <div>
            video {diagnostics ? `${diagnostics.videoW}x${diagnostics.videoH}` : "?"}
            {" · detect "}
            {diagnostics ? `${diagnostics.detW}x${diagnostics.detH}` : "?"}
          </div>
          {/* The lens discriminator: a different id across the two arms means
              the ladder swapped cameras, not just resolutions. */}
          <div className="truncate">cam {settings?.deviceId?.slice(0, 12) ?? "?"}</div>
        </div>
      </div>

      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center bg-black">
          <span className="material-symbols-outlined text-white text-4xl animate-spin">progress_activity</span>
        </div>
      )}

      <div className="absolute bottom-8 left-0 right-0 flex justify-center">
        {/*
          Never disabled on detection state: a missing box must not block a
          capture, because the review screen can fix any box.
        */}
        <button
          onClick={handleCapture}
          disabled={!ready}
          className={`w-20 h-20 rounded-full border-4 border-white flex items-center justify-center transition-all active:scale-90 ${
            documentDetected ? "bg-[#006d37]/80" : "bg-white/20"
          }`}
        >
          <div className={`w-14 h-14 rounded-full ${documentDetected ? "bg-[#006d37]" : "bg-white"}`} />
        </button>
      </div>

      <div className="absolute bottom-32 left-0 right-0 flex justify-center px-6">
        <span
          className={`text-xs font-bold px-3 py-1 rounded-full backdrop-blur-sm max-w-full truncate ${badgeClass}`}
        >
          {badgeText}
        </span>
      </div>
    </div>
  );
}
