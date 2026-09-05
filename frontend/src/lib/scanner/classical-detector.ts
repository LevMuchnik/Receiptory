import { getScanner, initScanner } from "@/lib/opencv-loader";
import { imageDataToCanvas } from "./canvas-utils";
import { convexHullArea, dist, interiorAngles, lerp, polygonArea } from "./geometry";
import type { Detector, DetectionOutcome, DetectionResult, Quad } from "./detector";

/** The subset of DetectionOutcome that `firstHardReject` can return. */
export type HardReject = Extract<
  DetectionOutcome,
  "rejected-area" | "rejected-aspect" | "rejected-angle" | "rejected-convexity"
>;

export interface ClassicalParams {
  shadowNorm: boolean;
  shadowBlurFraction: number;
  saturationPrior: boolean;
  saturationWeight: number;
  minAreaFraction: number;
  maxAreaFraction: number;
  minAspect: number;
  maxAspect: number;
  minAngleDeg: number;
  maxAngleDeg: number;
  wArea: number;
  wConvex: number;
  wUniform: number;
  wText: number;
}

export const CLASSICAL_DEFAULTS: ClassicalParams = {
  shadowNorm: true,
  shadowBlurFraction: 0.125,
  saturationPrior: true,
  saturationWeight: 0.35,
  minAreaFraction: 0.12,
  maxAreaFraction: 0.95,
  minAspect: 0.4,
  maxAspect: 12,
  minAngleDeg: 50,
  maxAngleDeg: 130,
  wArea: 0.35,
  wConvex: 0.25,
  wUniform: 0.30,
  wText: 0.25,
};

export class ClassicalDetector implements Detector {
  readonly name = "classical";

  getDefaultParams(): ClassicalParams {
    return { ...CLASSICAL_DEFAULTS };
  }

  async detect(image: ImageData, params: Partial<ClassicalParams> = {}): Promise<DetectionResult> {
    const p: ClassicalParams = { ...CLASSICAL_DEFAULTS, ...params };
    const start = performance.now();

    const preprocessed = preprocess(image, p);
    const canvas = imageDataToCanvas(preprocessed);

    let classified: ScanClassification;
    try {
      await initScanner();
      classified = classifyScanResult(await getScanner().scan(canvas, { mode: "detect" }));
    } catch (e) {
      classified = { rawCorners: null, error: scanThrowMessage(e) };
    }
    const { rawCorners, error } = classified;

    const quad = toQuad(rawCorners);
    if (!quad) {
      return {
        corners: null,
        score: 0,
        candidates: [],
        timingMs: performance.now() - start,
        // Corners present but malformed is a failure too; a plain miss above
        // has already set `error`, and a genuine "nothing found" cannot reach
        // here without one.
        error: error ?? (rawCorners ? "Scanner returned malformed corners" : undefined),
        outcome: error ? "error" : rawCorners ? "malformed-corners" : "no-contour",
      };
    }

    const metrics = computeMetrics(quad, image);
    const score = metrics.score(p);
    const reject = firstHardReject(metrics, p);
    // A rejected quad still travels in `candidates`. It is the only evidence
    // that scanic DID find something here, and the Lab needs it to tell a
    // too-tight threshold apart from a scene the scanner is blind on.
    return {
      corners: reject ? null : quad,
      score,
      candidates: [{ quad, score }],
      timingMs: performance.now() - start,
      outcome: reject ?? "accepted",
    };
  }
}

function preprocess(image: ImageData, p: ClassicalParams): ImageData {
  if (!p.shadowNorm && !p.saturationPrior) return image;

  const shadow = p.shadowNorm ? shadowNormalize(image, p.shadowBlurFraction) : null;
  const sat = p.saturationPrior ? saturationMask(image) : null;

  if (shadow && sat) return composeWeighted(shadow, sat, p.saturationWeight);
  return shadow ?? sat ?? image;
}

function shadowNormalize(image: ImageData, blurFraction: number): ImageData {
  const { width: w, height: h } = image;
  const gray = grayscaleAsRgba(image);
  const radius = Math.max(2, Math.round(w * blurFraction));
  const blurred = canvasBlur(gray, radius);

  const out = new Uint8ClampedArray(image.data.length);
  const src = gray.data;
  const blr = blurred.data;
  for (let i = 0; i < src.length; i += 4) {
    const g = src[i];
    const b = blr[i] || 1;
    const v = Math.min(255, Math.round((255 * g) / b));
    out[i] = v;
    out[i + 1] = v;
    out[i + 2] = v;
    out[i + 3] = 255;
  }
  return new ImageData(out, w, h);
}

function saturationMask(image: ImageData): ImageData {
  const { width: w, height: h, data } = image;
  const out = new Uint8ClampedArray(data.length);
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const s = max === 0 ? 0 : ((max - min) / max) * 255;
    const v = 255 - Math.round(s);
    out[i] = v;
    out[i + 1] = v;
    out[i + 2] = v;
    out[i + 3] = 255;
  }
  return new ImageData(out, w, h);
}

function composeWeighted(a: ImageData, b: ImageData, weightB: number): ImageData {
  const out = new Uint8ClampedArray(a.data.length);
  const wa = 1 - weightB;
  for (let i = 0; i < a.data.length; i += 4) {
    const v = Math.round(wa * a.data[i] + weightB * b.data[i]);
    out[i] = v;
    out[i + 1] = v;
    out[i + 2] = v;
    out[i + 3] = 255;
  }
  return new ImageData(out, a.width, a.height);
}

function grayscaleAsRgba(image: ImageData): ImageData {
  const out = new Uint8ClampedArray(image.data.length);
  const d = image.data;
  for (let i = 0; i < d.length; i += 4) {
    const v = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    out[i] = v;
    out[i + 1] = v;
    out[i + 2] = v;
    out[i + 3] = 255;
  }
  return new ImageData(out, image.width, image.height);
}

function canvasBlur(image: ImageData, radius: number): ImageData {
  const c = document.createElement("canvas");
  c.width = image.width;
  c.height = image.height;
  const ctx = c.getContext("2d")!;
  ctx.putImageData(image, 0, 0);

  const out = document.createElement("canvas");
  out.width = image.width;
  out.height = image.height;
  const octx = out.getContext("2d")!;
  octx.filter = `blur(${radius}px)`;
  octx.drawImage(c, 0, 0);
  return octx.getImageData(0, 0, image.width, image.height);
}

/** Scanic's default "nothing in this frame" message. Not a failure. */
const SCANIC_EMPTY_MESSAGE = "No document detected";

export interface ScanClassification {
  rawCorners: any;
  /** Set ONLY for genuine detector failure. Absent means "ran fine". */
  error?: string;
}

/**
 * Turn one scanic `scan(mode:"detect")` result into corners-or-error.
 *
 * Extracted from `detect()` so it is testable: `detect()` itself needs a canvas
 * for preprocessing, but this decision is pure and it is the one that decides
 * whether the viewfinder's error badge lights up.
 *
 * THE LOAD-BEARING CASE is `success: false` with scanic's own default message.
 * In detect mode scanic has exactly ONE such path: detectDocumentContour found
 * zero contours above minArea (scanic.js:1059-1066, propagated at :1366-1376).
 * That is an honest empty frame, and it fires on EVERY frame of a bare table.
 * Setting `error` there would pin the badge on permanently and destroy the only
 * signal meaning "the detector is broken" — the entire reason the error channel
 * exists. So stay silent on the default message; surface only a message scanic
 * does not normally emit.
 */
export function classifyScanResult(r: {
  success?: boolean;
  corners?: unknown;
  message?: string;
} | null | undefined): ScanClassification {
  if (!r) return { rawCorners: null, error: "Scanner returned no result" };
  if (r.success && r.corners) return { rawCorners: r.corners };
  if (!r.success) {
    return r.message && r.message !== SCANIC_EMPTY_MESSAGE
      ? { rawCorners: null, error: `Scanner: ${r.message}` }
      : { rawCorners: null };
  }
  // success:true with no corners is not a shape scanic documents; treat it as
  // a failure rather than a silent miss.
  return { rawCorners: null, error: "Scanner returned no corners" };
}

/** Message for a scan() that threw rather than resolved. Always a failure. */
export function scanThrowMessage(e: unknown): string {
  return `Scanner error: ${e instanceof Error ? e.message : String(e)}`;
}

export function toQuad(raw: any): Quad | null {
  if (!raw) return null;
  const tl = raw.topLeft ?? raw[0];
  const tr = raw.topRight ?? raw[1];
  const br = raw.bottomRight ?? raw[2];
  const bl = raw.bottomLeft ?? raw[3];
  if (!tl || !tr || !br || !bl) return null;
  // Number.isFinite, not typeof === "number": NaN and Infinity ARE numbers, and
  // they defeat every downstream guard silently. Every hard-reject comparison is
  // `x < t` / `x > t`, and all NaN comparisons are false, so a NaN quad passes
  // them all; the smoother's drift gate never fires; clampQuad returns NaN; the
  // viewfinder draws nothing while the badge says "Document detected"; and in
  // review the handles become un-grabbable with no escape offered.
  if ([tl, tr, br, bl].some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y))) return null;
  return { topLeft: tl, topRight: tr, bottomRight: br, bottomLeft: bl };
}

/** Exported so `firstHardReject` can be unit-tested against synthetic metrics. */
export interface Metrics {
  area: number;
  areaFraction: number;
  convexity: number;
  aspect: number;
  minAngle: number;
  maxAngle: number;
  uniformity: number;
  textDensity: number;
  score: (p: ClassicalParams) => number;
}

function computeMetrics(quad: Quad, image: ImageData): Metrics {
  const pts = [quad.topLeft, quad.topRight, quad.bottomRight, quad.bottomLeft];
  const area = polygonArea(pts);
  const imageArea = image.width * image.height;
  const areaFraction = area / Math.max(imageArea, 1);
  const hullArea = convexHullArea(pts);
  const convexity = hullArea > 0 ? Math.min(1, area / hullArea) : 0;

  const sides = [
    dist(pts[0], pts[1]),
    dist(pts[1], pts[2]),
    dist(pts[2], pts[3]),
    dist(pts[3], pts[0]),
  ];
  const wMean = (sides[0] + sides[2]) / 2;
  const hMean = (sides[1] + sides[3]) / 2;
  const aspect = wMean > 0 && hMean > 0 ? Math.max(wMean, hMean) / Math.min(wMean, hMean) : 0;

  const angles = interiorAngles(pts);
  const minAngle = Math.min(...angles);
  const maxAngle = Math.max(...angles);

  const { uniformity, textDensity } = sampleInterior(quad, image);

  return {
    area,
    areaFraction,
    convexity,
    aspect,
    minAngle,
    maxAngle,
    uniformity,
    textDensity,
    score(p: ClassicalParams): number {
      const areaTerm = Math.min(1, areaFraction / 0.5);
      return (
        p.wArea * areaTerm +
        p.wConvex * convexity +
        p.wUniform * uniformity -
        p.wText * textDensity
      );
    },
  };
}

/** Convexity floor. Still hardcoded; promoting it to a param is T10. */
const MIN_CONVEXITY = 0.85;

/**
 * The first hard reject a quad trips, or null if it survives all of them.
 *
 * Returns WHICH gate fired rather than a bare boolean, because "we found a quad
 * and threw it away" and "we found nothing" are the two hypotheses the Lab has
 * to tell apart, and a boolean collapses them into the same `corners: null`.
 * Order is deliberate and load-bearing for the degenerate case below: it reports
 * the first failure, not all of them.
 *
 * Exported for tests. It is pure — it takes `Metrics`, not `ImageData` — so it
 * runs in the node-env vitest suite with no DOM.
 *
 * ON `minAspect`: this reads like dead code and is not. `computeMetrics` sets
 * `aspect = max/min`, which is >= 1 for any real quad, so a 0.4 floor looks
 * unreachable. But the ternary at its definition falls back to `0` when either
 * mean side length is zero, so a DEGENERATE quad scores 0 and this bound is what
 * catches it. (`minAreaFraction` would catch it too, one line earlier — the
 * redundancy is cheap and it stops the guard order becoming load-bearing.)
 */
export function firstHardReject(m: Metrics, p: ClassicalParams): HardReject | null {
  if (m.areaFraction < p.minAreaFraction || m.areaFraction > p.maxAreaFraction) return "rejected-area";
  if (m.aspect < p.minAspect || m.aspect > p.maxAspect) return "rejected-aspect";
  if (m.minAngle < p.minAngleDeg || m.maxAngle > p.maxAngleDeg) return "rejected-angle";
  if (m.convexity < MIN_CONVEXITY) return "rejected-convexity";
  return null;
}

function sampleInterior(quad: Quad, image: ImageData): { uniformity: number; textDensity: number } {
  const samples = sampleQuadGray(quad, image, 32, 32);
  if (samples.length === 0) return { uniformity: 0, textDensity: 0 };

  let mean = 0;
  for (const v of samples) mean += v;
  mean /= samples.length;
  let variance = 0;
  for (const v of samples) variance += (v - mean) * (v - mean);
  variance /= samples.length;
  const stddev = Math.sqrt(variance);
  const uniformity = Math.max(0, 1 - stddev / 80);

  let dark = 0;
  for (const v of samples) if (v < mean - 35) dark++;
  const textDensity = Math.min(1, dark / samples.length / 0.25);

  return { uniformity, textDensity };
}

function sampleQuadGray(quad: Quad, image: ImageData, gridU: number, gridV: number): number[] {
  const out: number[] = [];
  const d = image.data;
  const w = image.width;
  const h = image.height;
  for (let v = 1; v < gridV; v++) {
    for (let u = 1; u < gridU; u++) {
      const fu = u / gridU;
      const fv = v / gridV;
      const top = lerp(quad.topLeft, quad.topRight, fu);
      const bot = lerp(quad.bottomLeft, quad.bottomRight, fu);
      const px = lerp(top, bot, fv);
      const x = Math.round(px.x);
      const y = Math.round(px.y);
      if (x < 0 || y < 0 || x >= w || y >= h) continue;
      const idx = (y * w + x) * 4;
      out.push(0.299 * d[idx] + 0.587 * d[idx + 1] + 0.114 * d[idx + 2]);
    }
  }
  return out;
}
