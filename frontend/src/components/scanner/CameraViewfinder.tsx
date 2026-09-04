import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCamera } from "@/lib/useCamera";
import type { Detector, Quad } from "@/lib/scanner/detector";
import { TemporalSmoother } from "@/lib/scanner/smoother";

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

/** Target detection resolution as a fraction of the video dimensions. */
const DETECTION_SCALE = 0.4;
/**
 * Floor on the gap between detections. `requestVideoFrameCallback` fires once
 * per video frame (~30-60Hz); the re-entrancy guard alone would run a ~50ms
 * main-thread detection roughly 5x more often than the old `rAF % 5` cadence
 * and make the preview worse, not better.
 */
const MIN_DETECT_INTERVAL_MS = 80;

export default function CameraViewfinder({
  detector,
  detectorParams,
  onCapture,
}: CameraViewfinderProps) {
  const { videoRef, error, ready } = useCamera();
  const svgRef = useRef<SVGSVGElement>(null);
  const overlayGroupRef = useRef<SVGGElement>(null);
  const polygonRef = useRef<SVGPolygonElement>(null);
  const cornerRefs = useRef<(SVGCircleElement | null)[]>([null, null, null, null]);
  /**
   * Detection-space corners. Deliberately a ref, not state: the overlay is
   * updated imperatively so a detection never costs a React render.
   */
  const cornersRef = useRef<Quad | null>(null);
  const detectionScaleRef = useRef(DETECTION_SCALE);
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

        const w = Math.max(1, Math.round(video.videoWidth * DETECTION_SCALE));
        const h = Math.max(1, Math.round(video.videoHeight * DETECTION_SCALE));
        if (offscreen.width !== w || offscreen.height !== h) {
          offscreen.width = w;
          offscreen.height = h;
        }
        detW = w;
        detH = h;
        detectionScaleRef.current = w / video.videoWidth;

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
    if (!video || !ready) return;
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
        <div className="text-center text-white space-y-3">
          <span className="material-symbols-outlined text-4xl text-[#ffdad6]">videocam_off</span>
          <p className="text-sm">{error}</p>
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
