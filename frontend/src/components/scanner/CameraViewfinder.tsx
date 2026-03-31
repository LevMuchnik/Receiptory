import { useCallback, useEffect, useRef, useState } from "react";
import { useCamera } from "@/lib/useCamera";
import { detectCorners } from "@/lib/opencv-loader";

interface CameraViewfinderProps {
  onCapture: (imageData: ImageData, corners: any | null) => void;
}

export default function CameraViewfinder({ onCapture }: CameraViewfinderProps) {
  const { videoRef, error, ready } = useCamera();
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const cornersRef = useRef<any>(null);
  const [documentDetected, setDocumentDetected] = useState(false);
  const frameCount = useRef(0);
  const detecting = useRef(false);

  useEffect(() => {
    if (!ready) return;
    const video = videoRef.current;
    if (!video) return;

    let animId: number;
    const offscreen = document.createElement("canvas");

    const tick = () => {
      frameCount.current++;

      // Detect every 5th frame, skip if previous detection still running
      if (frameCount.current % 5 === 0 && video.videoWidth > 0 && !detecting.current) {
        detecting.current = true;
        const scale = 0.4;
        offscreen.width = Math.round(video.videoWidth * scale);
        offscreen.height = Math.round(video.videoHeight * scale);
        const ctx = offscreen.getContext("2d")!;
        ctx.drawImage(video, 0, 0, offscreen.width, offscreen.height);
        const imageData = ctx.getImageData(0, 0, offscreen.width, offscreen.height);

        detectCorners(imageData).then((corners) => {
          cornersRef.current = corners;
          setDocumentDetected(!!corners);
          detecting.current = false;
        }).catch(() => {
          cornersRef.current = null;
          setDocumentDetected(false);
          detecting.current = false;
        });
      }

      // Draw overlay
      const overlay = overlayRef.current;
      if (overlay && video.videoWidth > 0) {
        overlay.width = overlay.clientWidth;
        overlay.height = overlay.clientHeight;
        const ctx = overlay.getContext("2d")!;
        ctx.clearRect(0, 0, overlay.width, overlay.height);

        const corners = cornersRef.current;
        if (corners) {
          const scale = 0.4;
          const scaleX = overlay.width / (video.videoWidth * scale);
          const scaleY = overlay.height / (video.videoHeight * scale);

          // Scanic returns corners as { topLeft, topRight, bottomLeft, bottomRight }
          const pts = [
            corners.topLeft || corners[0],
            corners.topRight || corners[1],
            corners.bottomRight || corners[2],
            corners.bottomLeft || corners[3],
          ].filter(Boolean);

          if (pts.length === 4) {
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
      }

      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [ready, videoRef]);

  const handleCapture = useCallback(() => {
    const video = videoRef.current;
    if (!video || !ready) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    onCapture(imageData, cornersRef.current);
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
          disabled={!ready}
          className={`w-20 h-20 rounded-full border-4 border-white flex items-center justify-center transition-all active:scale-90 ${
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
