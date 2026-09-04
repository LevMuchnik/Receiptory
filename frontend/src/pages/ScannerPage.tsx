import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { isWebView, isSecureContext } from "@/lib/platform";
import { initScanner, extractAndEnhance, terminateScanner } from "@/lib/opencv-loader";
import { buildPDF } from "@/lib/pdf-builder";
import { useScanner } from "@/lib/useScanner";
import type { ScannerState } from "@/lib/useScanner";
import type { ScannedPage } from "@/lib/pdf-builder";
import { ClassicalDetector } from "@/lib/scanner/classical-detector";
import { MLDetector } from "@/lib/scanner/ml-detector";
import type { Detector, Quad } from "@/lib/scanner/detector";
import { downscaleImageData, imageDataToCanvas } from "@/lib/scanner/canvas-utils";
import { scaleQuad, clampQuad } from "@/lib/scanner/geometry";
import { uploadTestFrame } from "@/lib/scanner/test-frame-upload";
import ScannerNav from "@/components/scanner/ScannerNav";
import CameraViewfinder from "@/components/scanner/CameraViewfinder";
import CaptureReview from "@/components/scanner/CaptureReview";
import WebViewWarning from "@/components/scanner/WebViewWarning";

interface ActiveConfig {
  detector: string;
  params: any;
}

/**
 * Longest edge, in pixels, that the review-entry re-detect is allowed to see.
 *
 * This downscale is mandatory, not an optimisation. `classical-detector`
 * computes its blur radius as `width * 0.125`, so a full-res 1920px frame means
 * a 240px canvas blur over 2Mpx plus ~8Mpx of per-pixel JS loops, synchronously,
 * on the shutter tap. That is exactly the shutter-latency regression that killed
 * PR #8 (success criterion 6). At ~800px the fresh detect costs about the same
 * as one live-loop detection.
 */
const REVIEW_DETECT_MAX_EDGE = 800;

export default function ScannerPage() {
  const navigate = useNavigate();
  const { state, pages, dispatch, addPage, clearPages } = useScanner();
  const [loadProgress, setLoadProgress] = useState("Initializing...");

  const classical = useMemo(() => new ClassicalDetector(), []);
  const ml = useMemo(() => new MLDetector(), []);
  const [detector, setDetector] = useState<Detector>(() => classical);
  const [detectorParams, setDetectorParams] = useState<any>(() => classical.getDefaultParams());

  /**
   * Monotonic id for the async work belonging to one review session. Bumped by
   * every capture and every corner commit; any result whose id is stale is
   * dropped instead of dispatched.
   */
  const jobRef = useRef(0);
  /**
   * Race guard for step 1.4. Set the moment the user grabs a handle; a fresh
   * detect that resolves afterwards is discarded so corners never move under
   * the user's finger. Reset at the start of each capture.
   */
  const handleTouchedRef = useRef(false);

  const insecure = !isSecureContext();
  const unsupported = isWebView();

  useEffect(() => {
    if (unsupported || insecure) return;
    let cancelled = false;

    async function init() {
      try {
        setLoadProgress("Loading scanner engine...");
        await initScanner();
        try {
          const cfg = await api.get<ActiveConfig>("/scanner/active-config");
          if (cfg?.detector === "ml") {
            setDetector(ml);
            setDetectorParams({ ...ml.getDefaultParams(), ...(cfg.params ?? {}) });
          } else {
            setDetector(classical);
            setDetectorParams({ ...classical.getDefaultParams(), ...(cfg?.params ?? {}) });
          }
        } catch {
          // Fall back to defaults if active config unreachable.
        }
        if (cancelled) return;
        dispatch({ type: "loaded" });
      } catch (err: any) {
        if (!cancelled) dispatch({ type: "error", message: err.message });
      }
    }

    init();
    return () => { cancelled = true; };
  }, [dispatch, unsupported, insecure, classical, ml]);

  /**
   * Run the warp for a given crop quad and publish the result.
   *
   * `detectionScale: 1` is passed EXPLICITLY. `corners` is in raw-frame pixels
   * now, and opencv-loader's default of 0.4 would multiply it by 2.5. The
   * parameter disappears entirely in Increment 2; until then, never rely on its
   * default. (Task T7.)
   */
  const runExtract = useCallback(
    async (raw: ImageData, corners: Quad | null, job: number) => {
      try {
        const result = await extractAndEnhance(raw, corners, 1);
        if (jobRef.current !== job) return;
        dispatch({ type: "extracted", extracted: result.original, enhanced: result.enhanced });
      } catch (err: any) {
        if (jobRef.current !== job) return;
        dispatch({ type: "error", message: `Capture failed: ${err.message}` });
      }
    },
    [dispatch],
  );

  /**
   * `emaCorners` arrive in DETECTION space; `detectionScale` is
   * detection-dimension / video-dimension, so dividing by it lands them in
   * raw-frame pixels — the one space CaptureReview and extractAndEnhance agree on.
   */
  const handleCapture = useCallback(
    async (imageData: ImageData, emaCorners: Quad | null, detectionScale: number) => {
      const job = ++jobRef.current;
      handleTouchedRef.current = false;

      const w = imageData.width;
      const h = imageData.height;

      const fromEma =
        emaCorners && detectionScale > 0
          ? clampQuad(scaleQuad(emaCorners, 1 / detectionScale), w, h)
          : null;

      // Review opens IMMEDIATELY on the raw frame with handles live. The warp
      // and the fresh detect fill in behind it; nothing blocks on them.
      dispatch({ type: "captured", raw: imageData, corners: fromEma });

      // Fresh detect on a downscaled copy — see REVIEW_DETECT_MAX_EDGE.
      let corners = fromEma;
      try {
        const { data: small, scale } = downscaleImageData(imageData, REVIEW_DETECT_MAX_EDGE);
        const fresh = await detector.detect(small, detectorParams);
        if (fresh.corners && scale > 0) {
          const inRawSpace = clampQuad(scaleQuad(fresh.corners, 1 / scale), w, h);
          // Race guard: drop the result outright if the user already grabbed a
          // handle (or left review), rather than yanking corners out from under
          // them. Their drag will commit its own corners and re-warp.
          if (jobRef.current === job && !handleTouchedRef.current) {
            corners = inRawSpace;
            dispatch({ type: "corners", corners: inRawSpace });
          }
        }
        // fresh.corners === null or fresh.error: keep the converted EMA quad
        // (or null, which CaptureReview renders as a 10% inset).
      } catch {
        // Same fallback. A detector failure must not cost the user their scan.
      }

      if (jobRef.current !== job) return;

      // Store the automatic corners alongside the frame, normalized 0-1 inside
      // uploadTestFrame so they share a space with ground truth.
      uploadTestFrame(imageData, detector.name, corners);

      // If a handle is already under a finger, do not warp yet: the pointerup
      // commit runs its own extract with the corners the user actually wants,
      // and warping the stale quad first would only burn a full-res pass.
      if (handleTouchedRef.current) return;

      await runExtract(imageData, corners, job);
    },
    [dispatch, detector, detectorParams, runExtract],
  );

  const handleDragStart = useCallback(() => {
    handleTouchedRef.current = true;
  }, []);

  const handleCornersCommit = useCallback(
    (corners: Quad) => {
      if (state.phase !== "reviewing") return;
      const raw = state.raw;
      const job = ++jobRef.current;
      dispatch({ type: "corners", corners });
      dispatch({ type: "extracted", extracted: null, enhanced: null });
      void runExtract(raw, corners, job);
    },
    [state, dispatch, runExtract],
  );

  /**
   * Escape hatch (success criterion 3: no scan is ever lost). Deliberately does
   * NOT go through extractAndEnhance: its own fallback ladder can end in a
   * detect+extract that crops, which is the one thing "use full frame" must
   * never do. The raw frame is handed through verbatim; `enhanced` is null, so
   * EnhancementToggle just shows it.
   */
  const handleUseFullFrame = useCallback(() => {
    if (state.phase !== "reviewing") return;
    const raw = state.raw;
    jobRef.current += 1;
    dispatch({
      type: "corners",
      corners: {
        topLeft: { x: 0, y: 0 },
        topRight: { x: raw.width, y: 0 },
        bottomRight: { x: raw.width, y: raw.height },
        bottomLeft: { x: 0, y: raw.height },
      },
    });
    dispatch({ type: "extracted", extracted: imageDataToCanvas(raw), enhanced: null });
  }, [state, dispatch]);

  /**
   * The canvas that actually becomes a PDF page. Never null — worst case, the
   * raw frame. `useEnhanced` is the review screen's toggle: picking "Original"
   * has to file the original, not merely preview it.
   */
  const finalCanvas = useCallback(
    (review: Extract<ScannerState, { phase: "reviewing" }>, useEnhanced: boolean) => {
      const preferred = useEnhanced ? review.enhanced : null;
      return preferred ?? review.extracted ?? imageDataToCanvas(review.raw);
    },
    [],
  );

  const handleSubmit = useCallback(
    async (currentRotation: number, useEnhanced: boolean) => {
      if (state.phase !== "reviewing") return;
      const canvas = finalCanvas(state, useEnhanced);
      jobRef.current += 1;
      dispatch({ type: "submit-start" });
      try {
        const allPages: ScannedPage[] = [...pages, { canvas, rotation: currentRotation }];

        const pdfBlob = buildPDF(allPages);
        const file = new File([pdfBlob], `scan_${Date.now()}.pdf`, { type: "application/pdf" });
        await api.upload([file]);

        clearPages();
        dispatch({ type: "submit-done" });
        navigate("/documents");
      } catch (err: any) {
        dispatch({ type: "error", message: `Upload failed: ${err.message}` });
      }
    },
    [state, pages, clearPages, dispatch, navigate, finalCanvas],
  );

  const handleAddPage = useCallback(
    (rotation: number, useEnhanced: boolean) => {
      if (state.phase !== "reviewing") return;
      const canvas = finalCanvas(state, useEnhanced);
      jobRef.current += 1;
      addPage(canvas, rotation);
    },
    [state, addPage, finalCanvas],
  );

  const handleRetake = useCallback(() => {
    jobRef.current += 1;
    dispatch({ type: "retake" });
  }, [dispatch]);

  const handleClose = useCallback(() => {
    jobRef.current += 1;
    clearPages();
    terminateScanner();
    navigate("/");
  }, [clearPages, navigate]);

  if (insecure) return <WebViewWarning reason="insecure" />;
  if (unsupported) return <WebViewWarning reason="webview" />;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black">
      <ScannerNav pageCount={pages.length} onClose={handleClose} />

      {state.phase === "loading" && (
        <div className="flex-1 flex flex-col items-center justify-center text-white gap-4">
          <span className="material-symbols-outlined text-4xl animate-spin">progress_activity</span>
          <p className="text-sm font-medium">{loadProgress}</p>
        </div>
      )}

      {state.phase === "viewfinder" && (
        <CameraViewfinder
          detector={detector}
          detectorParams={detectorParams}
          onCapture={handleCapture}
        />
      )}

      {state.phase === "reviewing" && (
        <CaptureReview
          raw={state.raw}
          corners={state.corners}
          extracted={state.extracted}
          enhanced={state.enhanced}
          pageCount={pages.length}
          onDragStart={handleDragStart}
          onCornersCommit={handleCornersCommit}
          onUseFullFrame={handleUseFullFrame}
          onRetake={handleRetake}
          onAddPage={handleAddPage}
          onSubmit={handleSubmit}
          onGiveUp={handleClose}
        />
      )}

      {state.phase === "submitting" && (
        <div className="flex-1 flex flex-col items-center justify-center text-white gap-4">
          <span className="material-symbols-outlined text-4xl animate-spin">progress_activity</span>
          <p className="text-sm font-medium">Creating PDF and uploading...</p>
        </div>
      )}

      {state.phase === "error" && (
        <div className="flex-1 flex flex-col items-center justify-center text-white gap-4 p-6">
          <span className="material-symbols-outlined text-4xl text-[#ffdad6]">error</span>
          <p className="text-sm text-center">{state.message}</p>
          <button
            onClick={() => {
              terminateScanner();
              initScanner().then(() => dispatch({ type: "loaded" }));
            }}
            className="px-6 py-3 bg-white/10 rounded-xl font-bold text-sm"
          >
            Try Again
          </button>
        </div>
      )}
    </div>
  );
}
