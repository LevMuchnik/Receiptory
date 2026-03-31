import { useCallback, useReducer, useState } from "react";
import type { ScannedPage } from "./pdf-builder";

type ScannerState =
  | { phase: "loading" }
  | { phase: "viewfinder" }
  | { phase: "reviewing"; capturedCanvas: HTMLCanvasElement; enhancedCanvas: HTMLCanvasElement | null }
  | { phase: "submitting" }
  | { phase: "error"; message: string };

type ScannerAction =
  | { type: "loaded" }
  | { type: "captured"; canvas: HTMLCanvasElement; enhanced: HTMLCanvasElement | null }
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
      return { phase: "reviewing", capturedCanvas: action.canvas, enhancedCanvas: action.enhanced };
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
  addPage: (canvas: HTMLCanvasElement, rotation: number) => void;
  clearPages: () => void;
}

export function useScanner(): UseScannerResult {
  const [state, dispatch] = useReducer(reducer, { phase: "loading" });
  const [pages, setPages] = useState<ScannedPage[]>([]);

  const addPage = useCallback((canvas: HTMLCanvasElement, rotation: number) => {
    setPages((prev) => [...prev, { canvas, rotation }]);
    dispatch({ type: "add-page", rotation });
  }, []);

  const clearPages = useCallback(() => {
    setPages([]);
  }, []);

  return { state, pages, dispatch, addPage, clearPages };
}
