import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import EnhancementToggle from "./EnhancementToggle";
import type { Pt, Quad } from "@/lib/scanner/detector";
import { clampPtToFrame, orderQuadByAngle, quadAreaFraction } from "@/lib/scanner/geometry";

/** Visible handle radius, in SCREEN pixels (converted to user units per paint). */
const HANDLE_R_PX = 15;
/** Grab radius, in SCREEN pixels. Thumb-sized, deliberately larger than the dot. */
const HANDLE_HIT_R_PX = 44;
/** Below this fraction of the frame the crop is degenerate; offer the escape. */
const MIN_AREA_FRACTION = 0.05;
/** How far the handle lifts away from the fingertip once a drag actually starts. */
const LIFT_OFFSET_PX = 26;
/** Movement (screen px) that turns a tap into a drag and triggers the lift. */
const LIFT_TRIGGER_PX = 6;

interface CaptureReviewProps {
  /** Full-resolution capture. Displayed un-warped; handles live on top of it. */
  raw: ImageData;
  /** Crop quad in RAW-FRAME PIXEL coordinates (same space as `raw`), or null. */
  corners: Quad | null;
  /** Warped output; null while the warp runs. */
  extracted: HTMLCanvasElement | null;
  enhanced: HTMLCanvasElement | null;
  pageCount: number;
  /**
   * Fired the instant a handle is grabbed. ScannerPage uses it as the race
   * guard: a fresh detect that resolves after this must be discarded, because
   * corners must never move under the user's finger.
   */
  onDragStart: () => void;
  /** Fired on pointerup with the repaired quad, in raw-frame pixels. */
  onCornersCommit: (corners: Quad) => void;
  /** Escape hatch: use the whole frame uncropped. */
  onUseFullFrame: () => void;
  onRetake: () => void;
  onAddPage: (rotation: number) => void;
  onSubmit: (rotation: number) => void;
  onGiveUp: () => void;
}

type Corners4 = [Pt, Pt, Pt, Pt];

function quadToArray(q: Quad): Corners4 {
  return [q.topLeft, q.topRight, q.bottomRight, q.bottomLeft];
}

/** Fallback quad when there is no detection: a 10% inset of the frame. */
function insetCorners(w: number, h: number, f = 0.1): Corners4 {
  const x0 = w * f;
  const x1 = w * (1 - f);
  const y0 = h * f;
  const y1 = h * (1 - f);
  return [
    { x: x0, y: y0 },
    { x: x1, y: y0 },
    { x: x1, y: y1 },
    { x: x0, y: y1 },
  ];
}

export default function CaptureReview({
  raw,
  corners,
  extracted,
  enhanced,
  pageCount,
  onDragStart,
  onCornersCommit,
  onUseFullFrame,
  onRetake,
  onAddPage,
  onSubmit,
  onGiveUp,
}: CaptureReviewProps) {
  const [rotation, setRotation] = useState(0);
  const [tab, setTab] = useState<"crop" | "result">("crop");
  const [dragging, setDragging] = useState(false);

  const rawCanvasRef = useRef<HTMLCanvasElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const polyRef = useRef<SVGPolygonElement>(null);
  const circleRefs = useRef<(SVGCircleElement | null)[]>([null, null, null, null]);

  /**
   * The live corner positions, in raw-frame pixels. This is a REF, not state:
   * a drag repaints through setAttribute at pointer rate, and React state is
   * touched only on pointerup. CaptureReview's own rotate memo and
   * EnhancementToggle's JPEG encode are both full-resolution operations; a
   * state-driven drag would run them at ~60Hz.
   */
  const cornersRef = useRef<Corners4>(insetCorners(raw.width, raw.height));
  /** Screen px per raw-frame px under the current `meet` fit. */
  const scaleRef = useRef(1);
  const dragRef = useRef<{ index: number; offset: Pt; start: Pt; lifted: boolean } | null>(null);

  // ---- painting ----------------------------------------------------------

  const paint = useCallback(() => {
    const svg = svgRef.current;
    const poly = polyRef.current;
    if (!svg || !poly) return;

    const ctm = svg.getScreenCTM();
    // `meet` scales both axes uniformly, so ctm.a is the whole story. It is 0
    // while the crop pane is display:none; the ResizeObserver repaints when it
    // comes back.
    if (ctm && ctm.a > 0) scaleRef.current = ctm.a;

    const pts = cornersRef.current;
    poly.setAttribute("points", pts.map((p) => `${p.x},${p.y}`).join(" "));

    const r = HANDLE_R_PX / scaleRef.current;
    for (let i = 0; i < 4; i++) {
      const c = circleRefs.current[i];
      if (!c) continue;
      c.setAttribute("cx", String(pts[i].x));
      c.setAttribute("cy", String(pts[i].y));
      c.setAttribute("r", String(r));
    }
  }, []);

  // Blit the raw frame into the display canvas. `object-contain` on the canvas
  // and `xMidYMid meet` on the overlay letterbox the same box identically, so
  // the two stay registered without any hand-written fit math.
  useEffect(() => {
    const c = rawCanvasRef.current;
    if (!c) return;
    c.width = raw.width;
    c.height = raw.height;
    c.getContext("2d")!.putImageData(raw, 0, 0);
  }, [raw]);

  // Adopt corners from props. During a drag the prop does not change, so this
  // never fights the finger; after commit the prop matches what we already have.
  useEffect(() => {
    cornersRef.current = corners ? quadToArray(corners) : insetCorners(raw.width, raw.height);
    paint();
  }, [corners, raw, paint]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => paint());
    ro.observe(svg);
    return () => ro.disconnect();
  }, [paint]);

  // ---- dragging ----------------------------------------------------------

  /** Screen point -> raw-frame point. The browser owns the viewBox transform. */
  const toFrame = useCallback((clientX: number, clientY: number): Pt | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const ctm = svg.getScreenCTM();
    if (!ctm || !(ctm.a > 0)) return null;
    scaleRef.current = ctm.a;
    const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  }, []);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (dragRef.current) return;
      const u = toFrame(e.clientX, e.clientY);
      if (!u) return;

      let best = -1;
      let bestD = Infinity;
      cornersRef.current.forEach((p, i) => {
        const d = Math.hypot(p.x - u.x, p.y - u.y);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      if (best < 0 || bestD * scaleRef.current > HANDLE_HIT_R_PX) return;

      dragRef.current = {
        index: best,
        // Grab offset: the corner keeps its distance from the fingertip instead
        // of teleporting under it. A pure tap therefore moves nothing.
        offset: { x: cornersRef.current[best].x - u.x, y: cornersRef.current[best].y - u.y },
        start: u,
        lifted: false,
      };
      // Keep tracking even when the finger leaves the SVG (or the viewport).
      try {
        svgRef.current?.setPointerCapture(e.pointerId);
      } catch {
        /* capture is best-effort */
      }
      setDragging(true);
      onDragStart();
      e.preventDefault();
    },
    [toFrame, onDragStart],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      const d = dragRef.current;
      if (!d) return;
      const u = toFrame(e.clientX, e.clientY);
      if (!u) return;

      // Once the gesture is unmistakably a drag, push the corner clear of the
      // fingertip so the user can see what they are aiming. Doing this on the
      // first real move rather than on pointerdown keeps a tap non-destructive.
      if (!d.lifted && Math.hypot(u.x - d.start.x, u.y - d.start.y) * scaleRef.current > LIFT_TRIGGER_PX) {
        d.lifted = true;
        const magPx = Math.hypot(d.offset.x, d.offset.y) * scaleRef.current;
        if (magPx < LIFT_OFFSET_PX) {
          if (magPx < 0.001) {
            d.offset = { x: 0, y: -LIFT_OFFSET_PX / scaleRef.current };
          } else {
            const k = LIFT_OFFSET_PX / magPx;
            d.offset = { x: d.offset.x * k, y: d.offset.y * k };
          }
        }
      }

      const next = clampPtToFrame({ x: u.x + d.offset.x, y: u.y + d.offset.y }, raw.width, raw.height);
      const pts = cornersRef.current;
      cornersRef.current = pts.map((p, i) => (i === d.index ? next : p)) as Corners4;
      paint();
    },
    [toFrame, paint, raw.width, raw.height],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      const d = dragRef.current;
      if (!d) return;
      dragRef.current = null;
      setDragging(false);
      try {
        svgRef.current?.releasePointerCapture(e.pointerId);
      } catch {
        /* capture may already be gone */
      }

      // Repair, do not block: sorting by angle around the centroid and
      // relabelling TL/TR/BR/BL makes a self-intersecting quad impossible to
      // represent. Dragging top-left past bottom-right just relabels corners.
      const quad = orderQuadByAngle(cornersRef.current);
      cornersRef.current = quadToArray(quad);
      paint();
      // The warp runs here and ONLY here — never during the drag.
      onCornersCommit(quad);
    },
    [paint, onCornersCommit],
  );

  // ---- derived -----------------------------------------------------------

  const areaFraction = useMemo(
    () => (corners ? quadAreaFraction(corners, raw.width, raw.height) : 1),
    [corners, raw.width, raw.height],
  );
  const degenerate = areaFraction < MIN_AREA_FRACTION;

  // Rotation applies to the EXTRACTED output only. If it also applied to the
  // handles, the corner-to-display transform would have to compose with it and
  // we would have invented a second coordinate-space bug.
  const rotatedOriginal = useMemo(
    () => (extracted ? rotateCanvas(extracted, rotation) : null),
    [extracted, rotation],
  );
  const rotatedEnhanced = useMemo(
    () => (enhanced ? rotateCanvas(enhanced, rotation) : null),
    [enhanced, rotation],
  );

  const rotate = (dir: number) => setRotation((r) => (r + dir + 360) % 360);
  const busy = !extracted;
  const rotateDisabled = dragging || busy;

  return (
    <div className="flex-1 flex flex-col bg-black overflow-hidden">
      <div className="relative flex-1 min-h-0">
        {/* Crop pane: the un-warped still with live handles. */}
        <div className={tab === "crop" ? "absolute inset-0" : "hidden"}>
          <canvas ref={rawCanvasRef} className="absolute inset-0 w-full h-full object-contain" />
          <svg
            ref={svgRef}
            viewBox={`0 0 ${raw.width} ${raw.height}`}
            preserveAspectRatio="xMidYMid meet"
            className="absolute inset-0 w-full h-full touch-none select-none"
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            <polygon
              ref={polyRef}
              points=""
              fill="rgba(0, 109, 55, 0.18)"
              stroke="#7ff0a8"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
            {[0, 1, 2, 3].map((i) => (
              <circle
                key={i}
                ref={(el) => {
                  circleRefs.current[i] = el;
                }}
                cx={0}
                cy={0}
                r={0}
                fill="rgba(0, 0, 0, 0.35)"
                stroke="#7ff0a8"
                strokeWidth={3}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>

          {degenerate && (
            <div className="absolute inset-x-3 bottom-3 rounded-xl bg-black/80 p-3 text-center backdrop-blur-sm">
              <p className="text-xs text-[#ffdad6] font-medium">
                That crop is almost empty. Drag the corners back out, or keep the whole frame.
              </p>
              <button
                onClick={onUseFullFrame}
                className="mt-2 w-full py-2 rounded-lg bg-white/15 text-white font-bold text-xs"
              >
                Use full frame
              </button>
            </div>
          )}
        </div>

        {/* Result pane: stays mounted once it exists so switching tabs does not
            re-encode the JPEG. */}
        {rotatedOriginal && (
          <div className={tab === "result" ? "absolute inset-0" : "hidden"}>
            <EnhancementToggle originalCanvas={rotatedOriginal} enhancedCanvas={rotatedEnhanced} />
          </div>
        )}
        {tab === "result" && !rotatedOriginal && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-white gap-3">
            <span className="material-symbols-outlined text-3xl animate-spin">progress_activity</span>
            <p className="text-xs font-medium">Preparing crop...</p>
          </div>
        )}
      </div>

      <div className="shrink-0 bg-[#191c1e] p-4 space-y-3 pb-6">
        <div className="flex justify-center">
          <div className="inline-flex rounded-full bg-white/10 p-1">
            {(["crop", "result"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-1.5 rounded-full text-xs font-bold ${
                  tab === t ? "bg-white/20 text-white" : "text-white/60"
                }`}
              >
                {t === "crop" ? "Adjust corners" : busy ? "Result..." : "Result"}
              </button>
            ))}
          </div>
        </div>

        <div className="flex justify-center gap-4">
          <button
            onClick={() => rotate(-90)}
            disabled={rotateDisabled}
            className="p-3 rounded-full bg-white/10 text-white active:bg-white/20 disabled:opacity-30"
          >
            <span className="material-symbols-outlined">rotate_left</span>
          </button>
          <button
            onClick={() => rotate(90)}
            disabled={rotateDisabled}
            className="p-3 rounded-full bg-white/10 text-white active:bg-white/20 disabled:opacity-30"
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
            disabled={busy}
            className="py-3 rounded-xl bg-white/10 text-white font-bold text-sm flex items-center justify-center gap-1.5 disabled:opacity-40"
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
            disabled={busy}
            className="py-3 rounded-xl bg-[#006d37] text-white font-bold text-sm flex items-center justify-center gap-1.5 disabled:opacity-40"
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
