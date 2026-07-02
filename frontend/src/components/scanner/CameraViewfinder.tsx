import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCamera } from "@/lib/useCamera";
import type { Detector, Quad } from "@/lib/scanner/detector";
import { TemporalSmoother } from "@/lib/scanner/smoother";

interface CameraViewfinderProps {
  detector: Detector;
  detectorParams: any;
  onCapture: (imageData: ImageData, corners: Quad | null, detectionScale: number) => void;
  onAutoCapture?: () => void;
}

// Live-detection input is downscaled for speed. We cap the long side at
// DETECTION_MAX_DIM so a 4K preview doesn't quadruple the detector's workload;
// BASE_DETECTION_SCALE is the ceiling applied to lower-resolution streams.
const BASE_DETECTION_SCALE = 0.4;
const DETECTION_MAX_DIM = 800;

export default function CameraViewfinder({
  detector,
  detectorParams,
  onCapture,
  onAutoCapture,
}: CameraViewfinderProps) {
  const { videoRef, error, ready, capturePhoto } = useCamera();
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const cornersRef = useRef<Quad | null>(null);
  const [documentDetected, setDocumentDetected] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const frameCount = useRef(0);
  const detecting = useRef(false);
  const smoother = useMemo(() => new TemporalSmoother(), []);
  const lastAutoCaptureRef = useRef(0);
  const detScaleRef = useRef(BASE_DETECTION_SCALE);
  // Synchronous guard against overlapping captures (takePhoto can take ~2s).
  // A ref, not just `capturing`, so a rapid double-tap before the re-render
  // can't slip a second call through a stale closure.
  const capturingRef = useRef(false);

  useEffect(() => {
    if (!ready) return;
    const video = videoRef.current;
    if (!video) return;

    let animId: number;
    const offscreen = document.createElement("canvas");

    const tick = () => {
      frameCount.current++;

      const detScale =
        video.videoWidth > 0
          ? Math.min(BASE_DETECTION_SCALE, DETECTION_MAX_DIM / Math.max(video.videoWidth, video.videoHeight))
          : BASE_DETECTION_SCALE;
      detScaleRef.current = detScale;

      if (frameCount.current % 5 === 0 && video.videoWidth > 0 && !detecting.current) {
        detecting.current = true;
        offscreen.width = Math.round(video.videoWidth * detScale);
        offscreen.height = Math.round(video.videoHeight * detScale);
        const ctx = offscreen.getContext("2d")!;
        ctx.drawImage(video, 0, 0, offscreen.width, offscreen.height);
        const imageData = ctx.getImageData(0, 0, offscreen.width, offscreen.height);
        const diag = Math.hypot(offscreen.width, offscreen.height);

        detector
          .detect(imageData, detectorParams)
          .then((result) => {
            smoother.push(result, diag);
            const ema = smoother.getEMA();
            cornersRef.current = ema;
            setDocumentDetected(!!ema);

            if (ema && onAutoCapture && smoother.shouldAutoCapture()) {
              const now = performance.now();
              if (now - lastAutoCaptureRef.current > 2000) {
                lastAutoCaptureRef.current = now;
                onAutoCapture();
              }
            }
            detecting.current = false;
          })
          .catch(() => {
            cornersRef.current = null;
            setDocumentDetected(false);
            detecting.current = false;
          });
      }

      const overlay = overlayRef.current;
      if (overlay && video.videoWidth > 0) {
        overlay.width = overlay.clientWidth;
        overlay.height = overlay.clientHeight;
        const ctx = overlay.getContext("2d")!;
        ctx.clearRect(0, 0, overlay.width, overlay.height);

        const corners = cornersRef.current;
        if (corners) {
          const scaleX = overlay.width / (video.videoWidth * detScale);
          const scaleY = overlay.height / (video.videoHeight * detScale);
          const pts = [corners.topLeft, corners.topRight, corners.bottomRight, corners.bottomLeft];

          ctx.beginPath();
          ctx.moveTo(pts[0].x * scaleX, pts[0].y * scaleY);
          for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i].x * scaleX, pts[i].y * scaleY);
          }
          ctx.closePath();
          ctx.fillStyle = "rgba(0, 109, 55, 0.15)";
          ctx.fill();
          ctx.strokeStyle = "#006d37";
          ctx.lineWidth = 3;
          ctx.stroke();

          for (const pt of pts) {
            ctx.beginPath();
            ctx.arc(pt.x * scaleX, pt.y * scaleY, 6, 0, Math.PI * 2);
            ctx.fillStyle = "#006d37";
            ctx.fill();
            ctx.strokeStyle = "white";
            ctx.lineWidth = 2;
            ctx.stroke();
          }
        }
      }

      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [ready, videoRef, detector, detectorParams, smoother, onAutoCapture]);

  const handleCapture = useCallback(async () => {
    if (capturingRef.current) return;
    capturingRef.current = true;
    setCapturing(true);
    try {
      const result = await capturePhoto();
      if (!result) return;
      if (result.source === "photo") {
        // Full-resolution still: the viewfinder corners were detected against
        // the preview stream (different resolution / field of view), so they
        // don't apply. Pass null to make extractAndEnhance re-detect on this
        // image.
        onCapture(result.imageData, null, 1);
      } else {
        onCapture(result.imageData, cornersRef.current, detScaleRef.current);
      }
    } finally {
      capturingRef.current = false;
      setCapturing(false);
    }
  }, [capturePhoto, onCapture]);

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

  return (
    <div className="flex-1 relative bg-black flex items-center justify-center overflow-hidden">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="absolute inset-0 w-full h-full object-cover"
      />
      <canvas ref={overlayRef} className="absolute inset-0 w-full h-full pointer-events-none" />

      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center bg-black">
          <span className="material-symbols-outlined text-white text-4xl animate-spin">progress_activity</span>
        </div>
      )}

      <div className="absolute bottom-8 left-0 right-0 flex justify-center">
        <button
          onClick={handleCapture}
          disabled={!ready || capturing}
          className={`w-20 h-20 rounded-full border-4 border-white flex items-center justify-center transition-all active:scale-90 disabled:opacity-60 ${
            documentDetected ? "bg-[#006d37]/80" : "bg-white/20"
          }`}
        >
          <div className={`w-14 h-14 rounded-full ${documentDetected ? "bg-[#006d37]" : "bg-white"}`} />
        </button>
      </div>

      <div className="absolute bottom-32 left-0 right-0 flex justify-center">
        <span className={`text-xs font-bold px-3 py-1 rounded-full backdrop-blur-sm ${
          documentDetected ? "bg-[#006d37]/80 text-white" : "bg-black/50 text-white/70"
        }`}>
          {documentDetected ? "Document detected" : "Position document in frame"}
        </span>
      </div>
    </div>
  );
}
