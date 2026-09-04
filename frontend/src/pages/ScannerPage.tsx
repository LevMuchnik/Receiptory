import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
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

      // If a handle is already under a finger, do not warp yet: the pointerup
      // commit runs its own extract with the corners the user actually wants,
      // and warping the stale quad first would only burn a full-res pass.
      if (handleTouchedRef.current) return;

      await runExtract(imageData, corners, job);
    },
    [dispatch, detector, detectorParams, runExtract],
  );

  /**
   * Add the frame to the scanner test corpus.
   *
   * Called when the user COMMITS a page (Add Page, or a successful Submit) --
   * never at the shutter. It used to fire on every capture, before the review
   * screen was even visible, which made "Retake" and "Discard All" untrue: a
   * receipt shot by mistake was already stored server-side. These are the
   * user's financial records; discard has to mean discard.
   *
   * Known gap, accepted deliberately: a page committed via Add Page is
   * collected at that moment, so a later "Discard All" does not unsend it.
   * Retaining raw ImageData for every queued page until submit would make the
   * multi-page memory problem materially worse (~8MB per page at 1080p, ~33MB
   * at 4K) and that is already a flagged risk. Add Page is an explicit decision
   * to keep the page, so collecting there is faithful enough.
   */
  const collectTestFrame = useCallback(
    (raw: ImageData, corners: Quad | null) => {
      // Corners are normalized 0-1 inside uploadTestFrame so they share a
      // space with ground truth.
      uploadTestFrame(raw, detector.name, corners);
    },
    [detector],
  );

  const handleDragStart = useCallback(() => {
    handleTouchedRef.current = true;
  }, []);

  /**
   * Release the drag guard once a commit has landed. It only gates the single
   * review-entry detect, but leaving it latched for the rest of the session is
   * a trap for anyone who later adds a second async detect.
   */
  const releaseDragGuard = useCallback(() => {
    handleTouchedRef.current = false;
  }, []);

  const handleCornersCommit = useCallback(
    (corners: Quad) => {
      if (state.phase !== "reviewing") return;
      const raw = state.raw;
      const job = ++jobRef.current;
      releaseDragGuard();
      dispatch({ type: "corners", corners });
      dispatch({ type: "extracted", extracted: null, enhanced: null });
      void runExtract(raw, corners, job);
    },
    [state, dispatch, runExtract, releaseDragGuard],
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
      const review = state;
      const canvas = finalCanvas(review, useEnhanced);
      jobRef.current += 1;
      dispatch({ type: "submit-start" });
      try {
        const allPages: ScannedPage[] = [...pages, { canvas, rotation: currentRotation }];

        // Yield a frame so the "Creating PDF and uploading..." state actually
        // paints. buildPDF is synchronous and does a full-resolution rotate plus
        // a JPEG encode PER PAGE; without this the UI is frozen for seconds with
        // nothing on screen, which reads as a crash.
        await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

        const pdfBlob = buildPDF(allPages);
        const file = new File([pdfBlob], `scan_${Date.now()}.pdf`, { type: "application/pdf" });
        const result = (await api.upload([file])) as {
          documents?: unknown[];
          duplicates?: unknown[];
        };

        // A duplicate is NOT an upload error: backend/api/upload.py:44-50,86
        // returns HTTP 200 with {"documents": [], "duplicates": [...]}. Without
        // this check the scan silently vanishes -- we would navigate to
        // /documents reporting success having filed nothing, which is exactly
        // the "no scan is ever lost" criterion failing quietly.
        const filed = Array.isArray(result?.documents) ? result.documents.length : 0;
        const dupes = Array.isArray(result?.duplicates) ? result.duplicates.length : 0;
        if (filed === 0 && dupes > 0) {
          dispatch({ type: "captured", raw: review.raw, corners: review.corners });
          if (review.extracted) {
            dispatch({ type: "extracted", extracted: review.extracted, enhanced: review.enhanced });
          }
          toast.info("Already filed — this document is a duplicate of one you have.");
          return;
        }

        // Collect only once the document is actually filed. A frame the user
        // retook or discarded is never sent, and a failed upload does not
        // collect either, so a retry cannot double-store.
        collectTestFrame(review.raw, review.corners);

        clearPages();
        dispatch({ type: "submit-done" });
        navigate("/documents");
      } catch (err: any) {
        // Do NOT drop the user's page. `submit-start` discarded the review
        // payload from the reducer, so restore it and put them back in review
        // with the error visible. Upload failure is a ROUTINE outcome here --
        // network blips, size limits -- and losing a scan to one is
        // not acceptable. `pages` is untouched, so queued pages survive too.
        dispatch({ type: "captured", raw: review.raw, corners: review.corners });
        if (review.extracted) {
          dispatch({ type: "extracted", extracted: review.extracted, enhanced: review.enhanced });
        }
        toast.error(`Upload failed: ${err.message}`);
      }
    },
    [state, pages, clearPages, dispatch, navigate, finalCanvas, collectTestFrame],
  );

  const handleAddPage = useCallback(
    (rotation: number, useEnhanced: boolean) => {
      if (state.phase !== "reviewing") return;
      const canvas = finalCanvas(state, useEnhanced);
      jobRef.current += 1;
      collectTestFrame(state.raw, state.corners);
      addPage(canvas, rotation);
    },
    [state, addPage, finalCanvas, collectTestFrame],
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
              initScanner()
                .then(() => dispatch({ type: "loaded" }))
                // Without a catch, a second init failure is an unhandled
                // rejection and the button looks inert.
                .catch((e) =>
                  dispatch({
                    type: "error",
                    message: `Scanner engine failed to start: ${e instanceof Error ? e.message : String(e)}`,
                  }),
                );
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
