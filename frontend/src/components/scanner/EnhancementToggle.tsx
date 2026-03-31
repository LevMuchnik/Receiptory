import { useState } from "react";

interface EnhancementToggleProps {
  originalCanvas: HTMLCanvasElement;
  enhancedCanvas: HTMLCanvasElement | null;
}

export default function EnhancementToggle({ originalCanvas, enhancedCanvas }: EnhancementToggleProps) {
  const [showEnhanced, setShowEnhanced] = useState(true);
  const displayCanvas = showEnhanced && enhancedCanvas ? enhancedCanvas : originalCanvas;

  return (
    <div className="relative w-full h-full flex items-center justify-center bg-black overflow-hidden">
      <img
        src={displayCanvas.toDataURL("image/jpeg", 0.92)}
        alt="Scanned document"
        className="max-w-full max-h-full object-contain"
      />
      {enhancedCanvas && (
        <button
          onClick={() => setShowEnhanced(!showEnhanced)}
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
