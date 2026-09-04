import { jsPDF } from "jspdf";

export interface ScannedPage {
  canvas: HTMLCanvasElement;
  rotation: number;
}

/**
 * Target DPI used to convert image pixels into PDF page millimetres.
 *
 * MUST track `page_render_dpi` in backend/config.py (default 200), which is what
 * backend/processing/pipeline.py uses to raster the PDF back into pixels for the
 * LLM. If the backend value changes, change this one too, or the round trip
 * below stops being 1:1.
 */
const TARGET_DPI = 200;

/**
 * PDF hard limit: a page dimension may not exceed 14400 pt = 200 in = 5080 mm.
 * We clamp a little under that so rounding can never push a page over.
 *
 * Unreachable in practice (5000mm at 200 DPI is a 39,370px edge) and kept only
 * so a future DPI change or a synthetic canvas cannot emit an invalid page.
 * Note it DOES break the 1:1 round trip if it ever fires.
 */
const MAX_PAGE_MM = 5000;

/** Fallback page for a degenerate (zero-width or zero-height) canvas. */
const FALLBACK_PAGE_MM = 10;

const MM_PER_INCH = 25.4;

/**
 * buildPDF — size every page to its own image so no pixels are thrown away.
 *
 *   ROUND TRIP (why this file matters)
 *
 *     capture canvas          PDF page                 backend raster
 *     2000 x 6000 px  --->  254.0 x 762.0 mm   --->   2000 x 6000 px
 *                     ^                        ^
 *                     |                        |
 *          px / TARGET_DPI * 25.4      mm / 25.4 * page_render_dpi
 *          (this file, 200 dpi)        (pipeline.py, 200 dpi)
 *
 *   Identity, as long as TARGET_DPI == page_render_dpi. Zero margins, so the
 *   image fills the page exactly and the render comes back pixel-for-pixel.
 *
 *   THE OLD BEHAVIOUR (the bug this replaces)
 *
 *     A4 (210x297mm) minus 5mm margins = 200 x 287 mm content box. A 1:3
 *     receipt hit the height-constrained branch:
 *
 *         drawW = 287 * (1/3) = 95.6 mm  --->  95.6/25.4 * 200 = ~753 px
 *
 *     ~753 px wide at the model REGARDLESS of capture resolution. A 500px crop
 *     was upscaled to it; a 4K crop would have been downsampled to it. That is
 *     what stopped the LLM from ever seeing a high-resolution receipt.
 */
/**
 * jsPDF SORTS a `format: [a, b]` array and then applies `orientation` to decide
 * which value is the width. Passing a literal [w, h] with orientation
 * "portrait" therefore produces a TRANSPOSED page whenever w > h.
 *
 * Verified against the pinned jspdf 4.2.1:
 *     new jsPDF({orientation:"portrait", format:[300,100]})  ->  page 100 x 300
 *     new jsPDF({orientation:"landscape", format:[300,100]}) ->  page 300 x 100
 *
 * An earlier revision of this file asserted the opposite in a comment and
 * hardcoded "portrait". Every landscape page was then created 100mm wide and
 * drawn at 300mm, silently clipping two thirds of the receipt off the right
 * edge before the backend ever rastered it for the LLM. Android hands back
 * landscape-oriented tracks under a portrait UI, so this was not a rare path.
 *
 * Derive the orientation from the dimensions and jsPDF's sort agrees with us.
 */
export function orientationFor(wMm: number, hMm: number): "portrait" | "landscape" {
  return wMm > hMm ? "landscape" : "portrait";
}

export function buildPDF(pages: ScannedPage[]): Blob {
  // Rotate first, then measure: applyRotation swaps w/h for 90/270, and the
  // page must be sized from the dimensions the image actually ends up with.
  const rendered = pages.map((page) => {
    const canvas = applyRotation(page.canvas, page.rotation);
    return { canvas, ...pageSizeMm(canvas.width, canvas.height) };
  });

  const first = rendered[0];
  const doc = new jsPDF(
    first
      ? {
          orientation: orientationFor(first.wMm, first.hMm),
          unit: "mm",
          format: [first.wMm, first.hMm],
        }
      : // No pages at all: hand back a valid, empty single-page PDF.
        { orientation: "portrait", unit: "mm", format: "a4" },
  );

  rendered.forEach(({ canvas, wMm, hMm }, i) => {
    if (i > 0) doc.addPage([wMm, hMm], orientationFor(wMm, hMm));

    // A degenerate canvas has no drawable pixels; it still gets a (tiny, blank)
    // page so page indices stay aligned with `pages`.
    if (canvas.width > 0 && canvas.height > 0) {
      const imgData = canvas.toDataURL("image/jpeg", 0.92);
      doc.addImage(imgData, "JPEG", 0, 0, wMm, hMm);
    }
  });

  return doc.output("blob");
}

/**
 * Pixels -> millimetres at TARGET_DPI, clamped to what the PDF format allows.
 *
 * Both dimensions are scaled by the SAME factor when clamping, so the aspect
 * ratio survives and the image still fills the page exactly.
 */
export function pageSizeMm(widthPx: number, heightPx: number): { wMm: number; hMm: number } {
  if (!(widthPx > 0) || !(heightPx > 0)) {
    // Zero-size or NaN canvas: a [0, 0] or [NaN, NaN] format would produce an
    // unopenable PDF, so fall back to a small blank page.
    return { wMm: FALLBACK_PAGE_MM, hMm: FALLBACK_PAGE_MM };
  }

  let wMm = (widthPx / TARGET_DPI) * MM_PER_INCH;
  let hMm = (heightPx / TARGET_DPI) * MM_PER_INCH;

  const overflow = Math.max(wMm, hMm) / MAX_PAGE_MM;
  if (overflow > 1) {
    wMm /= overflow;
    hMm /= overflow;
  }

  return { wMm, hMm };
}

function applyRotation(canvas: HTMLCanvasElement, degrees: number): HTMLCanvasElement {
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
