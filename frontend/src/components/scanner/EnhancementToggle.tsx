import { useEffect, useRef, useState } from "react";

interface EnhancementToggleProps {
  originalCanvas: HTMLCanvasElement;
  enhancedCanvas: HTMLCanvasElement | null;
  /**
   * Controlled by CaptureReview, not owned here. The choice has to reach
   * Submit: enhanceCanvas applies contrast(1.4) brightness(1.15), which can
   * blow out pale thermal print, so "Original" must actually file the
   * original rather than just preview it.
   */
  showEnhanced: boolean;
  onToggle: () => void;
}

/**
 * Encode a canvas once per canvas identity and hand back a blob URL.
 *
 * This replaces a `canvas.toDataURL("image/jpeg", 0.92)` that ran INSIDE
 * render: a full JPEG encode plus a multi-megabyte base64 string on every
 * single render pass. With a drag living in this component tree that is a real
 * hazard even at 1080p.
 *
 * Lifecycle: the URL created for a given canvas is revoked when that canvas is
 * replaced and again on unmount, so nothing leaks. The previous URL is revoked
 * only after the new one has been handed to React, so the <img> never points at
 * a revoked URL it has not yet loaded (an already-loaded image keeps its
 * decoded bitmap after revocation).
 */
function useCanvasObjectUrl(canvas: HTMLCanvasElement): string | null {
  const [url, setUrl] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    canvas.toBlob(
      (blob) => {
        if (cancelled || !blob) return;
        const next = URL.createObjectURL(blob);
        const prev = urlRef.current;
        urlRef.current = next;
        setUrl(next);
        if (prev) URL.revokeObjectURL(prev);
      },
      "image/jpeg",
      0.92,
    );
    return () => {
      cancelled = true;
    };
  }, [canvas]);

  // Unmount: release whatever URL is currently outstanding.
  useEffect(() => {
    return () => {
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, []);

  return url;
}

export default function EnhancementToggle({
  originalCanvas,
  enhancedCanvas,
  showEnhanced,
  onToggle,
}: EnhancementToggleProps) {
  const displayCanvas = showEnhanced && enhancedCanvas ? enhancedCanvas : originalCanvas;
  const src = useCanvasObjectUrl(displayCanvas);

  return (
    <div className="relative w-full h-full flex items-center justify-center bg-black overflow-hidden">
      {src && (
        <img src={src} alt="Scanned document" className="max-w-full max-h-full object-contain" />
      )}
      {enhancedCanvas && (
        <button
          onClick={onToggle}
          className="absolute top-4 right-4 flex items-center gap-1.5 bg-black/60 text-white px-3 py-1.5 rounded-full text-xs font-bold backdrop-blur-sm"
        >
          <span className="material-symbols-outlined text-sm">
            {showEnhanced ? "auto_fix_high" : "auto_fix_off"}
          </span>
          {showEnhanced ? "Enhanced" : "Original"}
        </button>
      )}
    </div>
  );
}
