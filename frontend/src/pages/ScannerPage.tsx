import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { isWebView, isSecureContext } from "@/lib/platform";
import { initScanner, extractAndEnhance, terminateScanner } from "@/lib/opencv-loader";
import { buildPDF } from "@/lib/pdf-builder";
import { useScanner } from "@/lib/useScanner";
import type { ScannedPage } from "@/lib/pdf-builder";
import ScannerNav from "@/components/scanner/ScannerNav";
import CameraViewfinder from "@/components/scanner/CameraViewfinder";
import CaptureReview from "@/components/scanner/CaptureReview";
import WebViewWarning from "@/components/scanner/WebViewWarning";

export default function ScannerPage() {
  const navigate = useNavigate();
  const { state, pages, dispatch, addPage, clearPages } = useScanner();
  const [loadProgress, setLoadProgress] = useState("Initializing...");

  const insecure = !isSecureContext();
  const unsupported = isWebView();

  useEffect(() => {
    if (unsupported || insecure) return;
    let cancelled = false;

    async function init() {
      try {
        setLoadProgress("Loading scanner engine...");
        await initScanner();
        if (cancelled) return;
        dispatch({ type: "loaded" });
      } catch (err: any) {
        if (!cancelled) dispatch({ type: "error", message: err.message });
      }
    }

    init();
    return () => { cancelled = true; };
  }, [dispatch, unsupported, insecure]);

  const handleCapture = useCallback(
    async (imageData: ImageData, corners: any) => {
      try {
        dispatch({ type: "captured", canvas: null as any, enhanced: null });

        const result = await extractAndEnhance(imageData, corners);

        dispatch({ type: "captured", canvas: result.original, enhanced: result.enhanced });
      } catch (err: any) {
        dispatch({ type: "error", message: `Capture failed: ${err.message}` });
      }
    },
    [dispatch]
  );

  const handleSubmit = useCallback(
    async (currentRotation: number) => {
      dispatch({ type: "submit-start" });
      try {
        const reviewState = state as {
          phase: "reviewing";
          capturedCanvas: HTMLCanvasElement;
          enhancedCanvas: HTMLCanvasElement | null;
        };
        const finalCanvas = reviewState.enhancedCanvas || reviewState.capturedCanvas;
        const allPages: ScannedPage[] = [...pages, { canvas: finalCanvas, rotation: currentRotation }];

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
    [state, pages, clearPages, dispatch, navigate]
  );

  const handleAddPage = useCallback(
    (rotation: number) => {
      const reviewState = state as {
        phase: "reviewing";
        capturedCanvas: HTMLCanvasElement;
        enhancedCanvas: HTMLCanvasElement | null;
      };
      const finalCanvas = reviewState.enhancedCanvas || reviewState.capturedCanvas;
      addPage(finalCanvas, rotation);
    },
    [state, addPage]
  );

  const handleClose = useCallback(() => {
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
        <CameraViewfinder onCapture={handleCapture} />
      )}

      {state.phase === "reviewing" && state.capturedCanvas && (
        <CaptureReview
          capturedCanvas={state.capturedCanvas}
          enhancedCanvas={state.enhancedCanvas}
          pageCount={pages.length}
          onRetake={() => dispatch({ type: "retake" })}
          onAddPage={handleAddPage}
          onSubmit={handleSubmit}
          onGiveUp={handleClose}
        />
      )}

      {(state.phase === "submitting" || (state.phase === "reviewing" && !state.capturedCanvas)) && (
        <div className="flex-1 flex flex-col items-center justify-center text-white gap-4">
          <span className="material-symbols-outlined text-4xl animate-spin">progress_activity</span>
          <p className="text-sm font-medium">
            {state.phase === "submitting" ? "Creating PDF and uploading..." : "Processing image..."}
          </p>
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
