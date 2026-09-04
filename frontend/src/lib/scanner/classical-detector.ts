import { getScanner, initScanner } from "@/lib/opencv-loader";
import { imageDataToCanvas } from "./canvas-utils";
import { convexHullArea, dist, interiorAngles, lerp, polygonArea } from "./geometry";
import type { Detector, DetectionResult, Quad } from "./detector";

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

    let rawCorners: any = null;
    let error: string | undefined;
    try {
      await initScanner();
      const r = await getScanner().scan(canvas, { mode: "detect" });
      if (r.success && r.corners) {
        rawCorners = r.corners;
      } else if (!r.success) {
        // scan() RESOLVED but reported failure. In detect mode Scanic has
        // exactly ONE success:false path: detectDocumentContour found zero
        // contours passing minArea, message "No document detected"
        // (scanic.js:1059-1066, propagated at :1366-1376).
        //
        // That is an honest empty frame, NOT a detector failure. It fires
        // constantly in normal use — every frame where the camera sees a bare
        // table, every frame caught mid-motion. Setting `error` here would
        // pin the error badge on permanently and destroy the only signal that
        // is supposed to mean "the detector is broken", which is the entire
        // reason the error channel exists.
        //
        // So: stay silent on Scanic's own default message. Surface only a
        // message it does not normally emit, which would be genuinely
        // unexpected and worth telling the user about.
        if (r.message && r.message !== "No document detected") {
          error = `Scanner: ${r.message}`;
        }
      } else {
        // success:true with no corners is not a shape Scanic documents;
        // treat it as a failure rather than a silent miss.
        error = "Scanner returned no corners";
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      rawCorners = null;
      error = `Scanner error: ${message}`;
    }

    const quad = normalizeQuad(rawCorners);
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
      };
    }

    const metrics = computeMetrics(quad, image);
    if (!passesHardRejects(metrics, p)) {
      return {
        corners: null,
        score: metrics.score(p),
        candidates: [{ quad, score: metrics.score(p) }],
        timingMs: performance.now() - start,
      };
    }

    const score = metrics.score(p);
    return {
      corners: quad,
      score,
      candidates: [{ quad, score }],
      timingMs: performance.now() - start,
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

function normalizeQuad(raw: any): Quad | null {
  if (!raw) return null;
  const tl = raw.topLeft ?? raw[0];
  const tr = raw.topRight ?? raw[1];
  const br = raw.bottomRight ?? raw[2];
  const bl = raw.bottomLeft ?? raw[3];
  if (!tl || !tr || !br || !bl) return null;
  if ([tl, tr, br, bl].some((p) => typeof p.x !== "number" || typeof p.y !== "number")) return null;
  return { topLeft: tl, topRight: tr, bottomRight: br, bottomLeft: bl };
}

interface Metrics {
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

function passesHardRejects(m: Metrics, p: ClassicalParams): boolean {
  if (m.areaFraction < p.minAreaFraction || m.areaFraction > p.maxAreaFraction) return false;
  if (m.aspect < p.minAspect || m.aspect > p.maxAspect) return false;
  if (m.minAngle < p.minAngleDeg || m.maxAngle > p.maxAngleDeg) return false;
  if (m.convexity < 0.85) return false;
  return true;
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
