import { jsPDF } from "jspdf";

export interface ScannedPage {
  canvas: HTMLCanvasElement;
  rotation: number;
}

export function buildPDF(pages: ScannedPage[]): Blob {
  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
  const pageW = 210;
  const pageH = 297;
  const margin = 5;
  const contentW = pageW - margin * 2;
  const contentH = pageH - margin * 2;

  pages.forEach((page, i) => {
    if (i > 0) doc.addPage();

    const rotated = applyRotation(page.canvas, page.rotation);
    const imgData = rotated.toDataURL("image/jpeg", 0.92);

    const imgAspect = rotated.width / rotated.height;
    const pageAspect = contentW / contentH;
    let drawW: number, drawH: number;

    if (imgAspect > pageAspect) {
      drawW = contentW;
      drawH = contentW / imgAspect;
    } else {
      drawH = contentH;
      drawW = contentH * imgAspect;
    }

    const x = margin + (contentW - drawW) / 2;
    const y = margin + (contentH - drawH) / 2;
    doc.addImage(imgData, "JPEG", x, y, drawW, drawH);
  });

  return doc.output("blob");
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
