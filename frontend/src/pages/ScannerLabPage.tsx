import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ScannerTestFrame } from "@/lib/api";
import { useCamera } from "@/lib/useCamera";
import { initScanner } from "@/lib/opencv-loader";
import { ClassicalDetector, CLASSICAL_DEFAULTS, type ClassicalParams } from "@/lib/scanner/classical-detector";
import { MLDetector, ML_DEFAULTS, type MLParams } from "@/lib/scanner/ml-detector";
import type { Detector, DetectionResult, Quad } from "@/lib/scanner/detector";
import { quadIoU, normalizeQuad, orderQuadByAngle } from "@/lib/scanner/geometry";
import { downscaleImageData, imageDataToCanvas } from "@/lib/scanner/canvas-utils";

/**
 * The Lab preview deliberately does NOT take the scanner's 4K raise.
 *
 * Every frame the Lab captures is re-encoded to a 1280px long edge on upload
 * (see `imageDataToJpegBlob`), and every frame it evaluates is downscaled again
 * by the detector. A 4K stream here would buy nothing but a 33MB ImageData held
 * in `loaded` state on a phone that the design doc already flags for memory
 * pressure. The scanner, which keeps full resolution for the final extract,
 * gets the raise; the Lab does not.
 *
 * The negotiated resolution is still surfaced under the viewfinder, so the
 * device's actual capability can be read off on-device without a debugger.
 */
const LAB_PREVIEW_WIDTH = 1920;
const LAB_PREVIEW_HEIGHT = 1080;

/**
 * Match `test-frame-upload.ts` — the corpus stores 1280px-long-edge JPEGs at
 * q0.85. Lab captures used to upload at full resolution and q0.9, which is both
 * a multi-megabyte upload (worse at 4K) and a corpus that mixes resolutions the
 * eval then can't compare across.
 */
const UPLOAD_LONG_EDGE = 1280;
const UPLOAD_JPEG_QUALITY = 0.85;

interface LoadedFrame {
  frameId: number | null;
  imageData: ImageData;
  imageUrl: string;
  width: number;
  height: number;
}

type DetectorKind = "classical" | "ml";

interface PanelState {
  kind: DetectorKind;
  classical: ClassicalParams;
  ml: MLParams;
  result: DetectionResult | null;
  evalReport: EvalReport | null;
}

interface EvalReport {
  /** Frames that actually produced a comparable detection. */
  count: number;
  hitRate: number;
  medianIoU: number;
  medianTimingMs: number;
  /**
   * Frames where the detector FAILED (DetectionResult.error) rather than
   * honestly finding nothing. These are excluded from the IoU distribution
   * entirely. Folding them in as 0-IoU misses is how a wrong model URL or a
   * dead Scanic init reads as "median IoU 0.00 -- the detector is bad", which
   * is the exact misreading this eval exists to prevent.
   */
  errors: number;
  /** Frames skipped because their stored ground truth would not parse. */
  unparsed: number;
}

const PALETTE_A = "#006d37";
const PALETTE_B = "#9b4dff";

function makePanel(): PanelState {
  return {
    kind: "classical",
    classical: { ...CLASSICAL_DEFAULTS },
    ml: { ...ML_DEFAULTS },
    result: null,
    evalReport: null,
  };
}

export default function ScannerLabPage() {
  const [scannerReady, setScannerReady] = useState(false);
  const [frames, setFrames] = useState<ScannerTestFrame[]>([]);
  const [loaded, setLoaded] = useState<LoadedFrame | null>(null);
  const [groundTruth, setGroundTruth] = useState<Quad | null>(null);
  const [activeBanner, setActiveBanner] = useState<string | null>(null);
  /** Bumped to remount LabCamera and retry camera acquisition from rung 1. */
  const [cameraAttempt, setCameraAttempt] = useState(0);

  const classical = useMemo(() => new ClassicalDetector(), []);
  const ml = useMemo(() => new MLDetector(), []);
  const detectorFor = useCallback((kind: DetectorKind): Detector => (kind === "ml" ? ml : classical), [classical, ml]);

  const [panelA, setPanelA] = useState<PanelState>(() => makePanel());
  const [panelB, setPanelB] = useState<PanelState>(() => ({ ...makePanel(), classical: { ...CLASSICAL_DEFAULTS, shadowNorm: false } }));

  useEffect(() => {
    initScanner().then(() => setScannerReady(true)).catch(() => setScannerReady(false));
    refreshFrames();
  }, []);

  const refreshFrames = useCallback(async () => {
    try {
      const { frames } = await api.listScannerTestFrames();
      setFrames(frames);
    } catch (e) {
      console.warn("Failed to list test frames:", e);
    }
  }, []);

  const loadFrame = useCallback(async (frame: ScannerTestFrame) => {
    const url = api.scannerTestFrameImageUrl(frame.id);
    const imageData = await fetchImageData(url);
    setLoaded({
      frameId: frame.id,
      imageData,
      imageUrl: url,
      width: imageData.width,
      height: imageData.height,
    });
    setPanelA((p) => ({ ...p, result: null }));
    setPanelB((p) => ({ ...p, result: null }));
    if (frame.ground_truth_json) {
      try {
        setGroundTruth(JSON.parse(frame.ground_truth_json));
      } catch {
        setGroundTruth(null);
      }
    } else {
      setGroundTruth(null);
    }
  }, []);

  const adoptCapturedFrame = useCallback(async (imageData: ImageData) => {
    const blob = await imageDataToJpegBlob(imageData);
    if (!blob) return;
    const result = await api.uploadScannerTestFrame(blob, {
      width: imageData.width,
      height: imageData.height,
    });
    await refreshFrames();
    const objectUrl = URL.createObjectURL(blob);
    setLoaded({
      frameId: result.id,
      imageData,
      imageUrl: objectUrl,
      width: imageData.width,
      height: imageData.height,
    });
    setPanelA((p) => ({ ...p, result: null }));
    setPanelB((p) => ({ ...p, result: null }));
    setGroundTruth(null);
  }, [refreshFrames]);

  const removeFrame = useCallback(async (id: number) => {
    await api.deleteScannerTestFrame(id);
    if (loaded?.frameId === id) {
      setLoaded(null);
      setGroundTruth(null);
      setPanelA((p) => ({ ...p, result: null }));
      setPanelB((p) => ({ ...p, result: null }));
    }
    await refreshFrames();
  }, [refreshFrames, loaded]);

  const runDetect = useCallback(async (panel: PanelState, set: (s: PanelState) => void) => {
    if (!loaded || !scannerReady) return;
    const det = detectorFor(panel.kind);
    const params = panel.kind === "ml" ? panel.ml : panel.classical;
    const result = await det.detect(loaded.imageData, params);
    set({ ...panel, result });
  }, [loaded, scannerReady, detectorFor]);

  const saveGroundTruth = useCallback(async () => {
    if (!loaded?.frameId || !groundTruth) return;
    const normalized = normalizeQuad(groundTruth, loaded.width, loaded.height);
    await api.patchScannerTestFrame(loaded.frameId, {
      ground_truth_json: JSON.stringify(normalized),
    });
    await refreshFrames();
    setActiveBanner("Ground truth saved");
    setTimeout(() => setActiveBanner(null), 2000);
  }, [loaded, groundTruth, refreshFrames]);

  const saveActiveConfig = useCallback(async (panel: PanelState) => {
    const params = panel.kind === "ml" ? panel.ml : panel.classical;
    await api.putScannerActiveConfig({
      detector: panel.kind,
      params: params as unknown as Record<string, unknown>,
    });
    setActiveBanner("Active config saved — reload the scanner page to apply.");
  }, []);

  const runEval = useCallback(async (panel: PanelState, set: (s: PanelState) => void) => {
    const annotated = frames.filter((f) => !!f.ground_truth_json);
    if (annotated.length === 0) {
      setActiveBanner("No annotated frames yet.");
      return;
    }
    const det = detectorFor(panel.kind);
    const params = panel.kind === "ml" ? panel.ml : panel.classical;
    const ious: number[] = [];
    const timings: number[] = [];
    let hits = 0;
    let errors = 0;
    let unparsed = 0;
    for (const f of annotated) {
      let gt: Quad;
      try {
        gt = JSON.parse(f.ground_truth_json!) as Quad;
      } catch {
        unparsed++;
        continue;
      }
      const imageData = await fetchImageData(api.scannerTestFrameImageUrl(f.id));
      const result = await det.detect(imageData, params);
      if (result.error) {
        // The detector broke; it did not look at this frame and miss. Scoring
        // it 0 would corrupt the median with a number that says nothing about
        // accuracy. Count it separately and surface it.
        errors++;
        continue;
      }
      timings.push(result.timingMs);
      // Order BOTH quads before measuring. quadIoU's polygonClip defines
      // "inside" as isLeft >= 0, so a quad wound the other way yields an empty
      // intersection and IoU 0 -- which reads as "the detector missed", the
      // exact misreading that killed the 2026-07-03 design. Ground truth is
      // hand-annotated and never re-wound, and detector output carries no
      // winding guarantee either.
      const iou = result.corners
        ? quadIoU(
            orderQuadByAngle(
              quadPoints(denormalizeQuadIfNormalized(gt, imageData.width, imageData.height)),
            ),
            orderQuadByAngle(quadPoints(result.corners)),
          )
        : 0;
      ious.push(iou);
      if (iou >= 0.85) hits++;
    }
    const report: EvalReport = {
      // Denominator is frames actually scored, not frames attempted. Dividing
      // by annotated.length while skipping frames silently under-reported the
      // hit rate.
      count: ious.length,
      hitRate: ious.length > 0 ? hits / ious.length : 0,
      medianIoU: median(ious),
      medianTimingMs: median(timings),
      errors,
      unparsed,
    };
    set({ ...panel, evalReport: report });
  }, [frames, detectorFor]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-headline font-bold text-primary">Scanner Lab</h1>
        <p className="text-sm text-muted-foreground">Capture frames, tune detector parameters, annotate ground truth.</p>
      </header>

      {activeBanner && (
        <div className="bg-primary/10 text-primary px-4 py-3 rounded-lg text-sm font-medium">
          {activeBanner}
        </div>
      )}

      <section className="bg-card rounded-xl p-5 shadow-[0_8px_32px_rgba(25,28,30,0.06)]">
        <h2 className="text-lg font-headline font-bold text-primary mb-4">1. Pick or capture a frame</h2>
        <div className="grid lg:grid-cols-2 gap-5">
          {/* `key` is the retry mechanism: bumping it remounts LabCamera, which
              tears down any half-acquired stream and walks the ladder afresh. */}
          <LabCamera
            key={cameraAttempt}
            onCapture={adoptCapturedFrame}
            onRetry={() => setCameraAttempt((n) => n + 1)}
          />
          <FramePicker frames={frames} selectedId={loaded?.frameId ?? null} onPick={loadFrame} onDelete={removeFrame} />
        </div>
      </section>

      {loaded && (
        <>
          <section className="bg-card rounded-xl p-5 shadow-[0_8px_32px_rgba(25,28,30,0.06)]">
            <h2 className="text-lg font-headline font-bold text-primary mb-4">2. Compare detectors</h2>
            <div className="grid lg:grid-cols-2 gap-5">
              <DetectorPanel
                title="Panel A"
                color={PALETTE_A}
                frame={loaded}
                groundTruth={groundTruth}
                panel={panelA}
                onChange={setPanelA}
                onDetect={() => runDetect(panelA, setPanelA)}
                onSaveActive={() => saveActiveConfig(panelA)}
                onRunEval={() => runEval(panelA, setPanelA)}
              />
              <DetectorPanel
                title="Panel B"
                color={PALETTE_B}
                frame={loaded}
                groundTruth={groundTruth}
                panel={panelB}
                onChange={setPanelB}
                onDetect={() => runDetect(panelB, setPanelB)}
                onSaveActive={() => saveActiveConfig(panelB)}
                onRunEval={() => runEval(panelB, setPanelB)}
              />
            </div>
          </section>

          <section className="bg-card rounded-xl p-5 shadow-[0_8px_32px_rgba(25,28,30,0.06)]">
            <h2 className="text-lg font-headline font-bold text-primary mb-4">3. Annotate ground truth</h2>
            <GroundTruthAnnotator
              frame={loaded}
              quad={groundTruth}
              onChange={setGroundTruth}
              onSave={saveGroundTruth}
              canSave={loaded.frameId !== null}
            />
          </section>
        </>
      )}
    </div>
  );
}

function LabCamera({
  onCapture,
  onRetry,
}: {
  onCapture: (image: ImageData) => void;
  onRetry: () => void;
}) {
  const { videoRef, error, ready, settings } = useCamera({
    width: LAB_PREVIEW_WIDTH,
    height: LAB_PREVIEW_HEIGHT,
  });

  const capture = useCallback(() => {
    const video = videoRef.current;
    if (!video || !ready) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    onCapture(ctx.getImageData(0, 0, canvas.width, canvas.height));
  }, [videoRef, ready, onCapture]);

  return (
    <div className="space-y-2">
      <div className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Live camera</div>
      <div className="relative bg-black rounded-lg overflow-hidden aspect-video">
        {error ? (
          // The stuck/failure messages end in "Tap to retry", so make that true:
          // remounting LabCamera re-runs the whole constraint ladder from rung 1.
          <button
            type="button"
            onClick={onRetry}
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white text-sm p-4 text-center"
          >
            <span className="material-symbols-outlined">videocam_off</span>
            {error}
          </button>
        ) : (
          <video ref={videoRef} autoPlay playsInline muted className="w-full h-full object-cover" />
        )}
        {!ready && !error && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40">
            <span className="material-symbols-outlined text-white animate-spin">progress_activity</span>
          </div>
        )}
      </div>
      {/* Negotiated resolution, not the requested one — measurement 8a wants the
          detection resolution recorded, and this is where it can be read. */}
      <div className="text-[11px] text-muted-foreground font-mono">
        {settings
          ? `granted ${settings.width ?? "?"}x${settings.height ?? "?"}` +
            (settings.frameRate ? ` @ ${Math.round(settings.frameRate)}fps` : "") +
            (settings.facingMode ? ` (${settings.facingMode})` : "")
          : `requested ${LAB_PREVIEW_WIDTH}x${LAB_PREVIEW_HEIGHT}…`}
      </div>
      <button
        onClick={capture}
        disabled={!ready}
        className="w-full py-2.5 rounded-lg bg-primary text-primary-foreground font-bold text-sm flex items-center justify-center gap-2 disabled:opacity-50"
      >
        <span className="material-symbols-outlined text-sm">add_a_photo</span>
        Add current frame to test set
      </button>
    </div>
  );
}

interface FramePickerProps {
  frames: ScannerTestFrame[];
  selectedId: number | null;
  onPick: (frame: ScannerTestFrame) => void;
  onDelete: (id: number) => void;
}

function FramePicker({ frames, selectedId, onPick, onDelete }: FramePickerProps) {
  return (
    <div className="space-y-2">
      <div className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Test set ({frames.length})</div>
      <div className="border border-border rounded-lg p-2 max-h-[360px] overflow-y-auto">
        {frames.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">Capture a frame to populate the test set.</p>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            {frames.map((f) => (
              <div key={f.id} className="relative group">
                <button
                  onClick={() => onPick(f)}
                  className={`block w-full aspect-square rounded-md overflow-hidden border-2 ${
                    selectedId === f.id ? "border-primary" : "border-transparent hover:border-muted-foreground"
                  }`}
                >
                  <img
                    src={api.scannerTestFrameImageUrl(f.id)}
                    alt={`Frame ${f.id}`}
                    className="w-full h-full object-cover"
                  />
                </button>
                <button
                  onClick={() => onDelete(f.id)}
                  className="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 flex items-center justify-center"
                  title="Delete frame"
                >
                  <span className="material-symbols-outlined text-sm">close</span>
                </button>
                {f.ground_truth_json && (
                  <span className="absolute bottom-1 left-1 text-[10px] bg-primary/80 text-primary-foreground px-1.5 rounded-sm">GT</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface DetectorPanelProps {
  title: string;
  color: string;
  frame: LoadedFrame;
  groundTruth: Quad | null;
  panel: PanelState;
  onChange: (p: PanelState) => void;
  onDetect: () => void;
  onSaveActive: () => void;
  onRunEval: () => void;
}

function DetectorPanel({
  title,
  color,
  frame,
  groundTruth,
  panel,
  onChange,
  onDetect,
  onSaveActive,
  onRunEval,
}: DetectorPanelProps) {
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const drawOverlay = useCallback(() => {
    const canvas = overlayRef.current;
    const img = imgRef.current;
    if (!canvas || !img || img.naturalWidth === 0) return;
    canvas.width = img.clientWidth;
    canvas.height = img.clientHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const sx = canvas.width / frame.width;
    const sy = canvas.height / frame.height;

    if (groundTruth) {
      drawQuad(
        ctx,
        denormalizeQuadIfNormalized(groundTruth, frame.width, frame.height),
        sx, sy, "#888", 1.5, true,
      );
    }

    const result = panel.result;
    if (result?.candidates) {
      for (const c of result.candidates) {
        if (c.quad === result.corners) continue;
        drawQuad(ctx, c.quad, sx, sy, color, 1, false, 0.3);
      }
    }
    if (result?.corners) {
      drawQuad(ctx, result.corners, sx, sy, color, 3, false);
    }
  }, [color, frame.width, frame.height, groundTruth, panel.result]);

  useEffect(() => {
    drawOverlay();
  }, [drawOverlay, frame.imageUrl]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-bold uppercase tracking-wider" style={{ color }}>{title}</div>
        <select
          value={panel.kind}
          onChange={(e) => onChange({ ...panel, kind: e.target.value as DetectorKind, result: null })}
          className="text-xs bg-muted border-none rounded-md px-2 py-1"
        >
          <option value="classical">classical</option>
          <option value="ml">ml</option>
        </select>
      </div>

      <div className="relative bg-black rounded-lg overflow-hidden">
        <img
          ref={imgRef}
          src={frame.imageUrl}
          alt="loaded frame"
          className="w-full h-auto"
          onLoad={drawOverlay}
        />
        <canvas ref={overlayRef} className="absolute inset-0 w-full h-full pointer-events-none" />
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        <Stat label="score" value={panel.result ? panel.result.score.toFixed(3) : "—"} />
        <Stat label="time" value={panel.result ? `${panel.result.timingMs.toFixed(0)} ms` : "—"} />
        <Stat label="candidates" value={String(panel.result?.candidates?.length ?? 0)} />
        <Stat
          label="status"
          value={
            panel.result?.error
              ? "error"
              : panel.result?.corners
                ? "accepted"
                : panel.result
                  ? "rejected"
                  : "—"
          }
        />
      </div>

      {panel.result?.error && (
        <div className="text-xs bg-[#ba1a1a]/10 text-[#ba1a1a] p-2 rounded-md font-medium">
          {panel.result.error}
        </div>
      )}

      {panel.evalReport && (
        <div className="text-xs grid grid-cols-2 gap-2 bg-muted/50 p-3 rounded-md">
          <span>Eval on {panel.evalReport.count} annotated frames</span>
          <span>Hit rate: <strong>{(panel.evalReport.hitRate * 100).toFixed(0)}%</strong></span>
          <span>Median IoU: <strong>{panel.evalReport.medianIoU.toFixed(3)}</strong></span>
          <span>Median time: <strong>{panel.evalReport.medianTimingMs.toFixed(0)} ms</strong></span>
          {panel.evalReport.errors > 0 && (
            <span className="text-[#ba1a1a] font-bold">
              {panel.evalReport.errors} detector error{panel.evalReport.errors === 1 ? "" : "s"} (excluded -- this number is not a clean baseline)
            </span>
          )}
          {panel.evalReport.unparsed > 0 && (
            <span className="text-[#ba1a1a]">{panel.evalReport.unparsed} unparsable ground truth</span>
          )}
        </div>
      )}

      <div className="flex gap-2">
        <button onClick={onDetect} className="flex-1 py-2 rounded-lg bg-primary text-primary-foreground font-bold text-sm">
          Detect
        </button>
        <button onClick={onRunEval} className="flex-1 py-2 rounded-lg bg-muted text-foreground font-bold text-sm border border-border">
          Run eval
        </button>
        <button onClick={onSaveActive} className="flex-1 py-2 rounded-lg bg-muted text-foreground font-bold text-sm border border-border">
          Save active
        </button>
      </div>

      {panel.kind === "classical" ? (
        <ParamControls
          params={panel.classical as unknown as Record<string, AnyParamValue>}
          order={CLASSICAL_PARAM_ORDER}
          onChange={(next) => onChange({ ...panel, classical: next as unknown as ClassicalParams })}
        />
      ) : (
        <ParamControls
          params={panel.ml as unknown as Record<string, AnyParamValue>}
          order={ML_PARAM_ORDER}
          onChange={(next) => onChange({ ...panel, ml: next as unknown as MLParams })}
        />
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="px-2 py-1 rounded-md bg-muted">
      {label}: <strong>{value}</strong>
    </span>
  );
}

type AnyParamValue = number | boolean | string;

const CLASSICAL_PARAM_ORDER: string[] = [
  "shadowNorm", "shadowBlurFraction",
  "saturationPrior", "saturationWeight",
  "minAreaFraction", "maxAreaFraction",
  "minAspect", "maxAspect",
  "minAngleDeg", "maxAngleDeg",
  "wArea", "wConvex", "wUniform", "wText",
];

const ML_PARAM_ORDER: string[] = [
  "modelUrl", "inputSize", "outputType", "scoreThreshold",
];

interface ParamControlsProps {
  params: Record<string, AnyParamValue>;
  order: string[];
  onChange: (p: Record<string, AnyParamValue>) => void;
}

function ParamControls({ params, order, onChange }: ParamControlsProps) {
  const setField = (key: string, value: AnyParamValue) => {
    onChange({ ...params, [key]: value });
  };
  return (
    <details className="bg-muted/50 rounded-lg p-3">
      <summary className="text-xs font-bold uppercase tracking-wider cursor-pointer text-muted-foreground">
        Parameters
      </summary>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 mt-3 text-xs">
        {order.map((key) => {
          const v = params[key];
          if (typeof v === "boolean") {
            return (
              <label key={key} className="flex items-center gap-2 col-span-2">
                <input type="checkbox" checked={v} onChange={(e) => setField(key, e.target.checked)} />
                <span className="font-mono">{key}</span>
              </label>
            );
          }
          if (typeof v === "number") {
            return (
              <label key={key} className="flex flex-col gap-0.5">
                <span className="font-mono text-[10px] text-muted-foreground">{key}</span>
                <input
                  type="number"
                  step="0.01"
                  value={v}
                  onChange={(e) => setField(key, Number(e.target.value))}
                  className="bg-card border border-border rounded-md px-2 py-1 text-xs"
                />
              </label>
            );
          }
          return (
            <label key={key} className="flex flex-col gap-0.5 col-span-2">
              <span className="font-mono text-[10px] text-muted-foreground">{key}</span>
              <input
                type="text"
                value={String(v ?? "")}
                onChange={(e) => setField(key, e.target.value)}
                className="bg-card border border-border rounded-md px-2 py-1 text-xs"
              />
            </label>
          );
        })}
      </div>
    </details>
  );
}

interface AnnotatorProps {
  frame: LoadedFrame;
  quad: Quad | null;
  onChange: (q: Quad) => void;
  onSave: () => void;
  canSave: boolean;
}

function GroundTruthAnnotator({ frame, quad, onChange, onSave, canSave }: AnnotatorProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [draggingCorner, setDraggingCorner] = useState<keyof Quad | null>(null);

  const initialQuad = useMemo<Quad>(() => quad ? denormalizeQuadIfNormalized(quad, frame.width, frame.height) : {
    topLeft: { x: frame.width * 0.15, y: frame.height * 0.15 },
    topRight: { x: frame.width * 0.85, y: frame.height * 0.15 },
    bottomRight: { x: frame.width * 0.85, y: frame.height * 0.85 },
    bottomLeft: { x: frame.width * 0.15, y: frame.height * 0.85 },
  }, [quad, frame.width, frame.height]);

  useEffect(() => {
    if (!quad) onChange(initialQuad);
  }, [quad, initialQuad, onChange]);

  const current = quad ? denormalizeQuadIfNormalized(quad, frame.width, frame.height) : initialQuad;

  const handlePointerDown = (corner: keyof Quad) => (e: React.PointerEvent) => {
    e.preventDefault();
    setDraggingCorner(corner);
    (e.target as Element).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!draggingCorner || !imgRef.current) return;
    const rect = imgRef.current.getBoundingClientRect();
    const xPx = ((e.clientX - rect.left) / rect.width) * frame.width;
    const yPx = ((e.clientY - rect.top) / rect.height) * frame.height;
    const clampedX = Math.max(0, Math.min(frame.width, xPx));
    const clampedY = Math.max(0, Math.min(frame.height, yPx));
    onChange({ ...current, [draggingCorner]: { x: clampedX, y: clampedY } });
  };

  const handlePointerUp = () => setDraggingCorner(null);

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Drag the four corners to mark what the detector should have returned. Saved as normalized coordinates.
      </p>
      <div
        ref={containerRef}
        className="relative bg-black rounded-lg overflow-hidden inline-block max-w-full"
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        <img ref={imgRef} src={frame.imageUrl} alt="annotation" className="block w-full h-auto select-none" />
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none"
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="none"
        >
          <polygon
            points={`${current.topLeft.x},${current.topLeft.y} ${current.topRight.x},${current.topRight.y} ${current.bottomRight.x},${current.bottomRight.y} ${current.bottomLeft.x},${current.bottomLeft.y}`}
            fill="rgba(255, 200, 0, 0.15)"
            stroke="#ffc800"
            strokeWidth={Math.max(2, frame.width / 400)}
          />
        </svg>
        {(["topLeft", "topRight", "bottomRight", "bottomLeft"] as (keyof Quad)[]).map((corner) => {
          const pt = current[corner];
          const left = (pt.x / frame.width) * 100;
          const top = (pt.y / frame.height) * 100;
          return (
            <button
              key={corner}
              onPointerDown={handlePointerDown(corner)}
              className="absolute w-6 h-6 -translate-x-1/2 -translate-y-1/2 rounded-full bg-yellow-400 border-2 border-white shadow-lg touch-none"
              style={{ left: `${left}%`, top: `${top}%`, cursor: "grab" }}
              title={corner}
            />
          );
        })}
      </div>
      <button
        onClick={onSave}
        disabled={!canSave}
        className="px-4 py-2 rounded-lg bg-primary text-primary-foreground font-bold text-sm disabled:opacity-50"
      >
        Save ground truth
      </button>
    </div>
  );
}

async function fetchImageData(url: string): Promise<ImageData> {
  const img = await loadImage(url);
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(img, 0, 0);
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = (e) => reject(e);
    img.src = url;
  });
}

/**
 * Encode a captured frame for upload, capped at `UPLOAD_LONG_EDGE`.
 *
 * The cap is the point: this used to encode at full resolution, so a 4K capture
 * became a multi-megabyte POST of pixels the corpus immediately throws away.
 * `downscaleImageData` returns the input untouched when it already fits, so a
 * 1080p capture costs nothing extra.
 *
 * The upload's `width`/`height` metadata still describes the ORIGINAL capture,
 * not this blob — same convention as `test-frame-upload.ts`, and the reason
 * stored corner data is normalized to 0-1.
 */
async function imageDataToJpegBlob(image: ImageData): Promise<Blob | null> {
  const { data } = downscaleImageData(image, UPLOAD_LONG_EDGE);
  const c = imageDataToCanvas(data);
  return new Promise((resolve) => c.toBlob((b) => resolve(b), "image/jpeg", UPLOAD_JPEG_QUALITY));
}

function drawQuad(
  ctx: CanvasRenderingContext2D,
  quad: Quad,
  sx: number,
  sy: number,
  color: string,
  lineWidth: number,
  dashed: boolean,
  fillAlpha = 0,
) {
  const pts = [quad.topLeft, quad.topRight, quad.bottomRight, quad.bottomLeft];
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(pts[0].x * sx, pts[0].y * sy);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x * sx, pts[i].y * sy);
  ctx.closePath();
  if (fillAlpha > 0) {
    ctx.fillStyle = colorWithAlpha(color, fillAlpha);
    ctx.fill();
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  if (dashed) ctx.setLineDash([6, 4]);
  ctx.stroke();
  ctx.restore();
}

function colorWithAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function denormalizeQuadIfNormalized(quad: Quad, w: number, h: number): Quad {
  const maxV = Math.max(
    quad.topLeft.x, quad.topLeft.y,
    quad.topRight.x, quad.topRight.y,
    quad.bottomRight.x, quad.bottomRight.y,
    quad.bottomLeft.x, quad.bottomLeft.y,
  );
  if (maxV > 1.5) return quad;
  return {
    topLeft: { x: quad.topLeft.x * w, y: quad.topLeft.y * h },
    topRight: { x: quad.topRight.x * w, y: quad.topRight.y * h },
    bottomRight: { x: quad.bottomRight.x * w, y: quad.bottomRight.y * h },
    bottomLeft: { x: quad.bottomLeft.x * w, y: quad.bottomLeft.y * h },
  };
}

// quadIoU / polygonClip moved to lib/scanner/geometry.ts so the accuracy metric
// that produces every "median IoU" number in the design docs is unit-testable.
// Its winding sensitivity is documented and regression-tested there.

function quadPoints(q: Quad) {
  return [q.topLeft, q.topRight, q.bottomRight, q.bottomLeft];
}

function median(xs: number[]): number {
  if (xs.length === 0) return 0;
  const sorted = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
