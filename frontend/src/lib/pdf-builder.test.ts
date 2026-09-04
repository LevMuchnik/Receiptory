import { describe, it, expect } from "vitest";
import { jsPDF } from "jspdf";
import { pageSizeMm } from "./pdf-builder";

/**
 * Regression guard for the page-transposition bug.
 *
 * jsPDF SORTS a `format: [a, b]` array and then applies `orientation` to decide
 * which value becomes the width. An earlier revision hardcoded
 * `orientation: "portrait"` under a comment claiming the array was taken
 * literally. It is not. Every page where wMm > hMm was created transposed and
 * then drawn at its untransposed size, silently clipping two thirds of the
 * receipt off the right edge — no exception, and a PDF that opens fine.
 *
 * These tests pin jsPDF's actual behaviour so the assumption cannot rot back in
 * on a version bump. jsPDF runs in Node, so no DOM is needed here; buildPDF
 * itself is not called because it needs canvas.toDataURL.
 */

const orientationFor = (w: number, h: number): "portrait" | "landscape" =>
  w > h ? "landscape" : "portrait";

function pageOf(wMm: number, hMm: number) {
  const doc = new jsPDF({
    orientation: orientationFor(wMm, hMm),
    unit: "mm",
    format: [wMm, hMm],
  });
  return {
    width: doc.internal.pageSize.getWidth(),
    height: doc.internal.pageSize.getHeight(),
  };
}

describe("jsPDF format/orientation contract", () => {
  it("DOCUMENTS the hazard: a literal [w,h] with portrait is transposed when w > h", () => {
    const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: [300, 100] });
    // Not 300x100. This is the bug, pinned so nobody reintroduces the assumption.
    expect(doc.internal.pageSize.getWidth()).toBeCloseTo(100, 1);
    expect(doc.internal.pageSize.getHeight()).toBeCloseTo(300, 1);
  });

  it("gives the requested page for a LANDSCAPE image when orientation is derived", () => {
    const { width, height } = pageOf(300, 100);
    expect(width).toBeCloseTo(300, 1);
    expect(height).toBeCloseTo(100, 1);
  });

  it("gives the requested page for a PORTRAIT image when orientation is derived", () => {
    const { width, height } = pageOf(100, 300);
    expect(width).toBeCloseTo(100, 1);
    expect(height).toBeCloseTo(300, 1);
  });

  it("handles the real Android landscape-track case (1920x1080 at 200 DPI)", () => {
    // The design doc notes Android often hands back a landscape-oriented track
    // under a portrait UI; "Use full frame" then produces exactly this.
    const { wMm, hMm } = pageSizeMm(1920, 1080);
    const { width, height } = pageOf(wMm, hMm);
    expect(width).toBeCloseTo(wMm, 3);
    expect(height).toBeCloseTo(hMm, 3);
    expect(width).toBeGreaterThan(height);
  });

  it("handles a square page without transposing", () => {
    const { width, height } = pageOf(150, 150);
    expect(width).toBeCloseTo(150, 1);
    expect(height).toBeCloseTo(150, 1);
  });

  it("keeps additional pages at their own requested size", () => {
    const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: [100, 300] });
    doc.addPage([300, 100], orientationFor(300, 100));
    expect(doc.internal.pageSize.getWidth()).toBeCloseTo(300, 1);
    expect(doc.internal.pageSize.getHeight()).toBeCloseTo(100, 1);
  });
});

describe("pageSizeMm", () => {
  it("converts pixels to mm at 200 DPI so the render round trip is 1:1", () => {
    // 2000px / 200dpi * 25.4 = 254mm; the backend rasters 254mm at 200dpi = 2000px.
    const { wMm, hMm } = pageSizeMm(2000, 6000);
    expect(wMm).toBeCloseTo(254, 6);
    expect(hMm).toBeCloseTo(762, 6);
    expect((wMm / 25.4) * 200).toBeCloseTo(2000, 6);
    expect((hMm / 25.4) * 200).toBeCloseTo(6000, 6);
  });

  it("beats the old A4 letterbox by 2.65x on a 1:3 receipt", () => {
    // Old behaviour: 287mm * (1/3) = 95.6mm -> ~753px at the model.
    const { wMm } = pageSizeMm(2000, 6000);
    expect((wMm / 25.4) * 200).toBeGreaterThan(753 * 2.6);
  });

  it("falls back to a small page for a degenerate canvas instead of NaN", () => {
    for (const [w, h] of [[0, 100], [100, 0], [NaN, 100], [-5, 10]]) {
      const { wMm, hMm } = pageSizeMm(w, h);
      expect(Number.isFinite(wMm)).toBe(true);
      expect(Number.isFinite(hMm)).toBe(true);
      expect(wMm).toBeGreaterThan(0);
      expect(hMm).toBeGreaterThan(0);
    }
  });

  it("clamps an absurd page under the PDF limit while preserving aspect", () => {
    const { wMm, hMm } = pageSizeMm(200000, 100000);
    expect(Math.max(wMm, hMm)).toBeLessThanOrEqual(5000);
    expect(wMm / hMm).toBeCloseTo(2, 6);
  });
});
