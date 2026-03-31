import { useState } from "react";
import EnhancementToggle from "./EnhancementToggle";

interface CaptureReviewProps {
  capturedCanvas: HTMLCanvasElement;
  enhancedCanvas: HTMLCanvasElement | null;
  pageCount: number;
  onRetake: () => void;
  onAddPage: (rotation: number) => void;
  onSubmit: (rotation: number) => void;
  onGiveUp: () => void;
}

export default function CaptureReview({
  capturedCanvas,
  enhancedCanvas,
  pageCount,
  onRetake,
  onAddPage,
  onSubmit,
  onGiveUp,
}: CaptureReviewProps) {
  const [rotation, setRotation] = useState(0);

  const rotate = (dir: number) => {
    setRotation((rotation + dir + 360) % 360);
  };

  const rotatedOriginal = rotateCanvas(capturedCanvas, rotation);
  const rotatedEnhanced = enhancedCanvas ? rotateCanvas(enhancedCanvas, rotation) : null;

  return (
    <div className="flex-1 flex flex-col bg-black overflow-hidden">
      <div className="flex-1 min-h-0">
        <EnhancementToggle originalCanvas={rotatedOriginal} enhancedCanvas={rotatedEnhanced} />
      </div>

      <div className="shrink-0 bg-[#191c1e] p-4 space-y-3 pb-6">
        <div className="flex justify-center gap-4">
          <button
            onClick={() => rotate(-90)}
            className="p-3 rounded-full bg-white/10 text-white active:bg-white/20"
          >
            <span className="material-symbols-outlined">rotate_left</span>
          </button>
          <button
            onClick={() => rotate(90)}
            className="p-3 rounded-full bg-white/10 text-white active:bg-white/20"
          >
            <span className="material-symbols-outlined">rotate_right</span>
          </button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={onRetake}
            className="py-3 rounded-xl bg-white/10 text-white font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">refresh</span>
            Retake
          </button>
          <button
            onClick={() => onAddPage(rotation)}
            className="py-3 rounded-xl bg-white/10 text-white font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">add_photo_alternate</span>
            Add Page
          </button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={onGiveUp}
            className="py-3 rounded-xl bg-[#93000a]/20 text-[#ffdad6] font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">delete</span>
            Discard All
          </button>
          <button
            onClick={() => onSubmit(rotation)}
            className="py-3 rounded-xl bg-[#006d37] text-white font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">send</span>
            Submit ({pageCount + 1} {pageCount + 1 === 1 ? "page" : "pages"})
          </button>
        </div>
      </div>
    </div>
  );
}

function rotateCanvas(canvas: HTMLCanvasElement, degrees: number): HTMLCanvasElement {
  if (degrees === 0) return canvas;
  const out = document.createElement("canvas");
  const ctx = out.getContext("2d")!;
  if (degrees === 90 || degrees === 270) {
    out.width = canvas.height;
    out.height = canvas.width;
  } else {
    out.width = canvas.width;
    out.height = canvas.height;
  }
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate((degrees * Math.PI) / 180);
  ctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
  return out;
}
