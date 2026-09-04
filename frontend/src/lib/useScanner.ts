import { useCallback, useReducer, useState } from "react";
import type { ScannedPage } from "./pdf-builder";
import type { Quad } from "./scanner/detector";

type ScannerState =
  | { phase: "loading" }
  | { phase: "viewfinder" }
  | {
      phase: "reviewing";
      /** The full-resolution frame exactly as it came off the video track. */
      raw: ImageData;
      /**
       * RAW-FRAME PIXEL COORDINATES — the same space as `raw`, i.e. x in
       * [0, raw.width] and y in [0, raw.height]. NOT detection space, NOT
       * screen space, NOT normalized 0-1.
       *
       * This whole feature exists because two coordinate spaces got confused,
       * so this field is the one that must never be ambiguous. Everything that
       * writes it converts first:
       *   - the EMA quad handed to onCapture is detection space -> divide by
       *     detectionScale before it lands here
       *   - the fresh review-entry detect runs on a ~800px downscale -> divide
       *     by that downscale's `scale` before it lands here
       *   - CaptureReview's drag handles read and write this space directly;
       *     the <svg viewBox> maps it to screen, and the browser owns that.
       * And everything that reads it passes `detectionScale: 1` to
       * extractAndEnhance, because the corners are already full-res.
       *
       * `null` means no detection: CaptureReview falls back to a 10% inset.
       */
      corners: Quad | null;
      /** Warped/cropped output. `null` while the warp is still running. */
      extracted: HTMLCanvasElement | null;
      /** Contrast-boosted copy of `extracted`, produced by extractAndEnhance. */
      enhanced: HTMLCanvasElement | null;
    }
  | { phase: "submitting" }
  | { phase: "error"; message: string };

type ScannerAction =
  | { type: "loaded" }
  /** Enter review immediately, on the raw frame alone. The warp fills in later. */
  | { type: "captured"; raw: ImageData; corners: Quad | null }
  /** Replace the crop quad (fresh detect landed, handle released, full-frame escape). */
  | { type: "corners"; corners: Quad | null }
  /** The warp finished (or was reset to pending with nulls). */
  | { type: "extracted"; extracted: HTMLCanvasElement | null; enhanced: HTMLCanvasElement | null }
  | { type: "retake" }
  | { type: "add-page"; rotation: number }
  | { type: "submit-start" }
  | { type: "submit-done" }
  | { type: "error"; message: string };

function reducer(state: ScannerState, action: ScannerAction): ScannerState {
  switch (action.type) {
    case "loaded":
      return { phase: "viewfinder" };
    case "captured":
      return {
        phase: "reviewing",
        raw: action.raw,
        corners: action.corners,
        extracted: null,
        enhanced: null,
      };
    case "corners":
      // Late async work (fresh detect, warp) can land after the user has left
      // review. Ignoring it here is the last line of defence behind the
      // sequence guard in ScannerPage.
      return state.phase === "reviewing" ? { ...state, corners: action.corners } : state;
    case "extracted":
      return state.phase === "reviewing"
        ? { ...state, extracted: action.extracted, enhanced: action.enhanced }
        : state;
    case "retake":
      return { phase: "viewfinder" };
    case "add-page":
      return { phase: "viewfinder" };
    case "submit-start":
      return { phase: "submitting" };
    case "submit-done":
      return { phase: "viewfinder" };
    case "error":
      return { phase: "error", message: action.message };
    default:
      return state;
  }
}

export type { ScannerState, ScannerAction };

export interface UseScannerResult {
  state: ScannerState;
  pages: ScannedPage[];
  dispatch: React.Dispatch<ScannerAction>;
  addPage: (page: ScannedPage) => void;
  clearPages: () => void;
}

export function useScanner(): UseScannerResult {
  const [state, dispatch] = useReducer(reducer, { phase: "loading" });
  const [pages, setPages] = useState<ScannedPage[]>([]);

  const addPage = useCallback((page: ScannedPage) => {
    // `page` is already rotated and JPEG-encoded — see ScannedPage. Queuing a
    // live canvas held ~30MB per page at 4K; five of those killed the tab.
    setPages((prev) => [...prev, page]);
    dispatch({ type: "add-page", rotation: 0 });
  }, []);

  const clearPages = useCallback(() => {
    setPages([]);
  }, []);

  return { state, pages, dispatch, addPage, clearPages };
}
