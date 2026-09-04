import { useCallback, useEffect, useRef, useState } from "react";

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
function useCanvasObjectUrl(canvas: HTMLCanvasElement): {
  url: string | null;
  onSettled: () => void;
} {
  const [url, setUrl] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  /** Superseded URL awaiting the new <img> to finish decoding before release. */
  const pendingRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    canvas.toBlob(
      (blob) => {
        if (cancelled || !blob) return;
        const next = URL.createObjectURL(blob);
        const prev = urlRef.current;
        urlRef.current = next;
        // Hand the stale URL to the <img>'s load handler instead of revoking it
        // here. Revoking synchronously assumed the previous image had FINISHED
        // loading; on rapid toggling of a multi-megapixel canvas the decode is
        // still in flight, and revoking mid-load errors it into a blank pane.
        // Revoke the previous URL only once the NEW image has settled.
        if (pendingRef.current && pendingRef.current !== prev) {
          URL.revokeObjectURL(pendingRef.current);
        }
        pendingRef.current = prev;
        setUrl(next);
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
      if (pendingRef.current) {
        URL.revokeObjectURL(pendingRef.current);
        pendingRef.current = null;
      }
    };
  }, []);

  // Called from the <img>'s onLoad/onError: the new bitmap is decoded (or has
  // definitively failed), so the previous URL is now safe to release.
  const onSettled = useCallback(() => {
    if (pendingRef.current) {
      URL.revokeObjectURL(pendingRef.current);
      pendingRef.current = null;
    }
  }, []);

  return { url, onSettled };
}

export default function EnhancementToggle({
  originalCanvas,
  enhancedCanvas,
  showEnhanced,
  onToggle,
}: EnhancementToggleProps) {
  const displayCanvas = showEnhanced && enhancedCanvas ? enhancedCanvas : originalCanvas;
  const { url: src, onSettled } = useCanvasObjectUrl(displayCanvas);

  return (
    <div className="relative w-full h-full flex items-center justify-center bg-black overflow-hidden">
      {src && (
        <img
          src={src}
          alt="Scanned document"
          className="max-w-full max-h-full object-contain"
          onLoad={onSettled}
          onError={onSettled}
        />
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
