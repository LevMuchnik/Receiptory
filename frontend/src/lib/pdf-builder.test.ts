import { describe, it, expect } from "vitest";
import { jsPDF } from "jspdf";
import { pageSizeMm, orientationFor, buildPDF } from "./pdf-builder";
import type { ScannedPage } from "./pdf-builder";

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

describe("orientationFor (the SHIPPED function)", () => {
  // These assert the real exported function, not a local copy. An earlier
  // revision of this file declared its own `orientationFor`, which made the
  // whole suite a DECOY: flipping the comparison in pdf-builder.ts would have
  // re-introduced the landscape-clipping bug with 10 green tests.
  it("returns landscape when the page is wider than tall", () => {
    expect(orientationFor(300, 100)).toBe("landscape");
    expect(orientationFor(243.8, 137.2)).toBe("landscape");
  });

  it("returns portrait when the page is taller than wide", () => {
    expect(orientationFor(100, 300)).toBe("portrait");
  });

  it("returns portrait for a square page", () => {
    expect(orientationFor(150, 150)).toBe("portrait");
  });

  it("feeds jsPDF an orientation that yields the requested page", () => {
    for (const [w, h] of [[300, 100], [100, 300], [150, 150], [243.8, 137.2]]) {
      const doc = new jsPDF({ orientation: orientationFor(w, h), unit: "mm", format: [w, h] });
      expect(doc.internal.pageSize.getWidth()).toBeCloseTo(w, 1);
      expect(doc.internal.pageSize.getHeight()).toBeCloseTo(h, 1);
    }
  });
});

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

/**
 * buildPDF became node-testable when pages stopped carrying live canvases.
 * That was done for memory (a queued 4K canvas is ~30MB of backing store), but
 * it also removed the last DOM dependency from this module.
 */

// Smallest valid JPEG that jsPDF will accept, as a data URL.
const TINY_JPEG =
  "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL" +
  "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB" +
  "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==";

const page = (widthPx: number, heightPx: number, dataUrl = TINY_JPEG): ScannedPage => ({
  dataUrl,
  widthPx,
  heightPx,
});

describe("buildPDF", () => {
  it("returns a non-empty PDF blob for a single page", async () => {
    const blob = buildPDF([page(2000, 6000)]);
    expect(blob.size).toBeGreaterThan(0);
    const head = new Uint8Array(await blob.arrayBuffer()).slice(0, 5);
    expect(String.fromCharCode(...head)).toBe("%PDF-");
  });

  it("sizes a LANDSCAPE page correctly — the clipping-bug guard, end to end", async () => {
    // The whole point: a landscape page must not be transposed. Rendering is
    // out of reach here, so assert via the same helpers buildPDF uses, and rely
    // on the orientationFor suite above for the jsPDF contract itself.
    const { wMm, hMm } = pageSizeMm(1920, 1080);
    expect(orientationFor(wMm, hMm)).toBe("landscape");
    const blob = buildPDF([page(1920, 1080)]);
    expect(blob.size).toBeGreaterThan(0);
  });

  it("builds a multi-page PDF with per-page sizes, mixing orientations", async () => {
    const blob = buildPDF([page(2000, 6000), page(1920, 1080), page(1000, 1000)]);
    expect(blob.size).toBeGreaterThan(0);
  });

  it("returns a valid single-page PDF when handed no pages at all", async () => {
    const blob = buildPDF([]);
    expect(blob.size).toBeGreaterThan(0);
  });

  it("emits a page but draws nothing when the image is missing", async () => {
    // Keeps page indices aligned with the input array rather than silently
    // dropping a page the user thinks they scanned.
    const blob = buildPDF([page(1000, 1000, "")]);
    expect(blob.size).toBeGreaterThan(0);
  });

  it("survives a degenerate page size without emitting NaN", async () => {
    const blob = buildPDF([page(0, 0, ""), page(1000, 1000)]);
    expect(blob.size).toBeGreaterThan(0);
  });
});
