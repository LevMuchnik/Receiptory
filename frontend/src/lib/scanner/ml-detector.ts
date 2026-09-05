import type { Detector, DetectionResult, Quad } from "./detector";

export interface MLParams {
  modelUrl: string;
  inputSize: number;
  outputType: "regression" | "heatmap";
  scoreThreshold: number;
}

export const ML_DEFAULTS: MLParams = {
  modelUrl: "",
  inputSize: 128,
  outputType: "heatmap",
  scoreThreshold: 0.3,
};

interface SessionLike {
  inputNames: readonly string[];
  outputNames: readonly string[];
  run: (feeds: Record<string, unknown>) => Promise<Record<string, unknown>>;
}

export class MLDetector implements Detector {
  readonly name = "ml";
  private session: SessionLike | null = null;
  private sessionUrl: string | null = null;
  private loading: Promise<void> | null = null;

  getDefaultParams(): MLParams {
    return { ...ML_DEFAULTS };
  }

  async detect(image: ImageData, params: Partial<MLParams> = {}): Promise<DetectionResult> {
    const p: MLParams = { ...ML_DEFAULTS, ...params };
    if (!p.modelUrl) {
      // Failure, not an empty table: the detector was asked to run with nothing
      // to run. Silently returning "no document" hides a misconfiguration.
      return {
        corners: null,
        score: 0,
        candidates: [],
        timingMs: 0,
        error: "No ML model configured",
      };
    }
    const start = performance.now();

    try {
      await this.ensureSession(p.modelUrl);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      console.warn("ML model load failed:", e);
      return {
        corners: null,
        score: 0,
        candidates: [],
        timingMs: performance.now() - start,
        error: `ML model load failed: ${message}`,
      };
    }
    if (!this.session) {
      // ensureSession() resolved without throwing yet left no session — a
      // concurrent load for a different URL cleared it, or the runtime handed
      // back nothing. Either way inference cannot run: a failure, not a miss.
      return {
        corners: null,
        score: 0,
        candidates: [],
        timingMs: performance.now() - start,
        error: "ML model load failed: session unavailable",
      };
    }

    const lb = letterbox(image, p.inputSize);
    const inputTensor = await makeInputTensor(lb.data, p.inputSize);
    const feeds: Record<string, unknown> = { [this.session.inputNames[0]]: inputTensor };

    let result: Record<string, unknown>;
    try {
      result = await this.session.run(feeds);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      console.warn("ML inference failed:", e);
      return {
        corners: null,
        score: 0,
        candidates: [],
        timingMs: performance.now() - start,
        error: `ML inference failed: ${message}`,
      };
    }

    const parsed = parseOutput(result, this.session.outputNames, p);
    if (!parsed) {
      // parseOutput only returns null when the tensor does not match the shape
      // this adapter expects (missing output, <8 regression values, <4 heatmap
      // channels). That is an adapter/model mismatch — a failure — never the
      // model's way of saying "no document here"; a model that found nothing
      // still returns a well-shaped tensor with low peaks, which lands below
      // scoreThreshold below and yields corners: null with no error.
      return {
        corners: null,
        score: 0,
        candidates: [],
        timingMs: performance.now() - start,
        error: "ML output did not match the expected tensor shape",
      };
    }
    const { quad: quadInInput, score } = parsed;
    const corners = unletterbox(quadInInput, lb, image.width, image.height);

    const accepted = score >= p.scoreThreshold;
    return {
      corners: accepted ? corners : null,
      score,
      candidates: [{ quad: corners, score }],
      timingMs: performance.now() - start,
      // A below-threshold score is this detector's only "found something, threw
      // it away" path — the ML analogue of a classical hard reject, and the
      // reason the Lab's breakdown can compare the two detectors at all.
      outcome: accepted ? "accepted" : "rejected-score",
    };
  }

  private async ensureSession(url: string): Promise<void> {
    if (this.session && this.sessionUrl === url) return;
    if (this.loading && this.sessionUrl === url) {
      await this.loading;
      return;
    }
    this.session = null;
    this.sessionUrl = url;
    this.loading = (async () => {
      const ort = await import("onnxruntime-web");
      const session = await ort.InferenceSession.create(url, {
        executionProviders: ["webgpu", "wasm"],
      });
      this.session = session as unknown as SessionLike;
    })();
    try {
      await this.loading;
    } finally {
      this.loading = null;
    }
  }
}

interface LetterboxInfo {
  data: ImageData;
  scale: number;
  offsetX: number;
  offsetY: number;
  inputSize: number;
}

function letterbox(image: ImageData, size: number): LetterboxInfo {
  const src = document.createElement("canvas");
  src.width = image.width;
  src.height = image.height;
  src.getContext("2d")!.putImageData(image, 0, 0);

  const scale = size / Math.max(image.width, image.height);
  const newW = Math.round(image.width * scale);
  const newH = Math.round(image.height * scale);
  const offsetX = Math.round((size - newW) / 2);
  const offsetY = Math.round((size - newH) / 2);

  const out = document.createElement("canvas");
  out.width = size;
  out.height = size;
  const ctx = out.getContext("2d")!;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, size, size);
  ctx.drawImage(src, offsetX, offsetY, newW, newH);
  return {
    data: ctx.getImageData(0, 0, size, size),
    scale,
    offsetX,
    offsetY,
    inputSize: size,
  };
}

async function makeInputTensor(image: ImageData, size: number): Promise<unknown> {
  const ort = await import("onnxruntime-web");
  const pixels = image.data;
  const planeSize = size * size;
  const float32 = new Float32Array(3 * planeSize);
  for (let i = 0; i < planeSize; i++) {
    const p = i * 4;
    float32[i] = pixels[p] / 255;
    float32[i + planeSize] = pixels[p + 1] / 255;
    float32[i + 2 * planeSize] = pixels[p + 2] / 255;
  }
  return new ort.Tensor("float32", float32, [1, 3, size, size]);
}

function parseOutput(
  result: Record<string, unknown>,
  outputNames: readonly string[],
  params: MLParams,
): { quad: Quad; score: number } | null {
  const primary = result[outputNames[0]] as { data: Float32Array; dims: readonly number[] } | undefined;
  if (!primary) return null;
  const data = primary.data;
  const dims = primary.dims;

  if (params.outputType === "regression") {
    if (data.length < 8) return null;
    const s = params.inputSize;
    return {
      quad: {
        topLeft: { x: data[0] * s, y: data[1] * s },
        topRight: { x: data[2] * s, y: data[3] * s },
        bottomRight: { x: data[4] * s, y: data[5] * s },
        bottomLeft: { x: data[6] * s, y: data[7] * s },
      },
      score: 1.0,
    };
  }

  // Heatmap: expected dims like [1, 4, h, w] or [4, h, w].
  let channels = 4;
  let h = params.inputSize;
  let w = params.inputSize;
  if (dims.length === 4) {
    channels = dims[1];
    h = dims[2];
    w = dims[3];
  } else if (dims.length === 3) {
    channels = dims[0];
    h = dims[1];
    w = dims[2];
  }
  if (channels < 4) return null;

  const points: { x: number; y: number; peak: number }[] = [];
  const planeSize = h * w;
  for (let c = 0; c < 4; c++) {
    let bestIdx = 0;
    let best = -Infinity;
    const off = c * planeSize;
    for (let i = 0; i < planeSize; i++) {
      const v = data[off + i];
      if (v > best) {
        best = v;
        bestIdx = i;
      }
    }
    const y = Math.floor(bestIdx / w);
    const x = bestIdx % w;
    points.push({
      x: (x / w) * params.inputSize,
      y: (y / h) * params.inputSize,
      peak: best,
    });
  }
  const score = points.reduce((s, p) => s + p.peak, 0) / 4;
  return {
    quad: {
      topLeft: points[0],
      topRight: points[1],
      bottomRight: points[2],
      bottomLeft: points[3],
    },
    score,
  };
}

function unletterbox(quad: Quad, lb: LetterboxInfo, origW: number, origH: number): Quad {
  const map = (pt: { x: number; y: number }) => ({
    x: clamp((pt.x - lb.offsetX) / lb.scale, 0, origW),
    y: clamp((pt.y - lb.offsetY) / lb.scale, 0, origH),
  });
  return {
    topLeft: map(quad.topLeft),
    topRight: map(quad.topRight),
    bottomRight: map(quad.bottomRight),
    bottomLeft: map(quad.bottomLeft),
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
