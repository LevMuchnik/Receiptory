# Mobile Document Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a client-side document scanning feature that activates on Android devices, with live viewfinder, document boundary detection, image enhancement, multi-page capture, and PDF submission.

**Architecture:** New `/scan` route with a fullscreen scanner UI. OpenCV.js WASM (~8MB) is lazy-loaded only when the scanner opens. Corner detection runs in a Web Worker to keep the UI smooth. Captured pages are enhanced client-side (white balance, shadow removal, CLAHE), assembled into a PDF via jsPDF, and submitted to the existing `POST /api/upload` endpoint.

**Tech Stack:** jscanify (document detection/extraction), OpenCV.js WASM (image processing), jsPDF (PDF assembly), Web Workers (off-thread detection), getUserMedia (camera access)

---

## File Structure

```
frontend/src/
  pages/
    ScannerPage.tsx              # Main scanner page - orchestrates the full flow
  components/scanner/
    CameraViewfinder.tsx         # Live camera feed with boundary overlay
    CaptureReview.tsx            # Post-capture review (rotate, retake, add page, submit)
    EnhancementToggle.tsx        # Before/after enhancement comparison toggle
    ScannerNav.tsx               # Minimal top bar (back, page count)
    WebViewWarning.tsx           # Warning banner for unsupported WebViews
  lib/
    useCamera.ts                 # Hook: getUserMedia lifecycle management
    useScanner.ts                # Hook: orchestrates scan state machine
    opencv-loader.ts             # Lazy-loads OpenCV.js WASM, returns cv instance
    image-enhance.ts             # Enhancement pipeline (white balance, shadow, CLAHE)
    pdf-builder.ts               # Assembles captured pages into a PDF blob
    platform.ts                  # Android/WebView detection utilities
  workers/
    corner-detect.worker.ts      # Web Worker: runs jscanify corner detection off-thread
  App.tsx                        # Modified: add /scan route, Android redirect logic
  components/Sidebar.tsx         # Modified: add Scan button to mobile bottom nav
```

**Dependencies to install:**
- `jscanify` (document boundary detection — tiny wrapper, loads OpenCV.js separately)
- `jspdf` (PDF generation from canvas images)

OpenCV.js is loaded at runtime from a self-hosted WASM file (copied into `public/`), NOT installed via npm — this avoids bundling 8MB into the main JS bundle.

---

### Task 1: Platform Detection Utilities

**Files:**
- Create: `frontend/src/lib/platform.ts`
- Test: manual — utility functions, tested via Task 10 integration

- [ ] **Step 1: Create platform detection module**

```typescript
// frontend/src/lib/platform.ts

export function isAndroid(): boolean {
  return /Android/i.test(navigator.userAgent);
}

export function isMobile(): boolean {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || window.innerWidth < 768;
}

export function isWebView(): boolean {
  const ua = navigator.userAgent;
  // Detect common Android WebViews that lack getUserMedia
  if (/wv\)/.test(ua)) return true;                    // Android WebView marker
  if (/FB(AN|AV)/.test(ua)) return true;               // Facebook in-app browser
  if (/Instagram/.test(ua)) return true;                // Instagram in-app browser
  if (/Line\//.test(ua)) return true;                   // LINE in-app browser
  if (/Twitter/.test(ua) || /X\//.test(ua)) return true; // X/Twitter in-app browser
  return false;
}

export function hasGetUserMedia(): boolean {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/platform.ts
git commit -m "feat(scanner): add platform detection utilities"
```

---

### Task 2: OpenCV.js Lazy Loader

**Files:**
- Create: `frontend/src/lib/opencv-loader.ts`
- Download: OpenCV.js WASM into `frontend/public/opencv/`

- [ ] **Step 1: Download OpenCV.js build files**

```bash
cd frontend/public
mkdir -p opencv
curl -fsSL "https://docs.opencv.org/4.10.0/opencv.js" -o opencv/opencv.js
```

This is the single-file build (~8MB) that includes WASM inline. It will only be loaded when the scanner module requests it.

- [ ] **Step 2: Create the lazy loader module**

```typescript
// frontend/src/lib/opencv-loader.ts

let cvInstance: any = null;
let loadingPromise: Promise<any> | null = null;

export function loadOpenCV(): Promise<any> {
  if (cvInstance) return Promise.resolve(cvInstance);
  if (loadingPromise) return loadingPromise;

  loadingPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/opencv/opencv.js";
    script.async = true;

    script.onload = () => {
      // OpenCV.js sets window.cv, but WASM init is async
      const checkReady = () => {
        const cv = (window as any).cv;
        if (cv && cv.Mat) {
          cvInstance = cv;
          resolve(cv);
        } else if (cv && cv.onRuntimeInitialized !== undefined) {
          cv.onRuntimeInitialized = () => {
            cvInstance = cv;
            resolve(cv);
          };
        } else {
          setTimeout(checkReady, 50);
        }
      };
      checkReady();
    };

    script.onerror = () => {
      loadingPromise = null;
      reject(new Error("Failed to load OpenCV.js"));
    };

    document.head.appendChild(script);
  });

  return loadingPromise;
}

export function getCV(): any {
  if (!cvInstance) throw new Error("OpenCV not loaded yet — call loadOpenCV() first");
  return cvInstance;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/public/opencv/opencv.js frontend/src/lib/opencv-loader.ts
git commit -m "feat(scanner): add OpenCV.js lazy loader with WASM"
```

---

### Task 3: Corner Detection Web Worker

**Files:**
- Create: `frontend/src/workers/corner-detect.worker.ts`
- Modify: `frontend/vite.config.ts` (if needed for worker support — Vite supports `?worker` imports natively)

- [ ] **Step 1: Create the Web Worker**

The worker receives image data, runs jscanify's corner detection via OpenCV, and posts back corner coordinates.

```typescript
// frontend/src/workers/corner-detect.worker.ts

let cv: any = null;
let scanner: any = null;

self.onmessage = async (e: MessageEvent) => {
  const { type, data } = e.data;

  if (type === "init") {
    // Load OpenCV inside the worker
    importScripts("/opencv/opencv.js");
    await new Promise<void>((resolve) => {
      const check = () => {
        if ((self as any).cv && (self as any).cv.Mat) {
          cv = (self as any).cv;
          resolve();
        } else if ((self as any).cv) {
          (self as any).cv.onRuntimeInitialized = () => {
            cv = (self as any).cv;
            resolve();
          };
        } else {
          setTimeout(check, 50);
        }
      };
      check();
    });

    // Import jscanify (it expects cv on globalThis)
    importScripts("https://cdn.jsdelivr.net/npm/jscanify@1.4.2/dist/jscanify.min.js");
    scanner = new (self as any).jscanify();
    self.postMessage({ type: "ready" });
    return;
  }

  if (type === "detect" && cv && scanner) {
    try {
      const { imageData, width, height } = data;
      const mat = new cv.Mat(height, width, cv.CV_8UC4);
      mat.data.set(new Uint8Array(imageData));

      const contour = scanner.findPaperContour(mat);
      if (contour && contour.size().height >= 4) {
        const points = scanner.getCornerPoints(contour);
        self.postMessage({
          type: "corners",
          data: {
            topLeft: { x: points.topLeftCorner.x, y: points.topLeftCorner.y },
            topRight: { x: points.topRightCorner.x, y: points.topRightCorner.y },
            bottomLeft: { x: points.bottomLeftCorner.x, y: points.bottomLeftCorner.y },
            bottomRight: { x: points.bottomRightCorner.x, y: points.bottomRightCorner.y },
          },
        });
        contour.delete();
      } else {
        self.postMessage({ type: "no-corners" });
        if (contour) contour.delete();
      }

      mat.delete();
    } catch (err) {
      self.postMessage({ type: "no-corners" });
    }
  }
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/workers/corner-detect.worker.ts
git commit -m "feat(scanner): add corner detection Web Worker"
```

---

### Task 4: Image Enhancement Pipeline

**Files:**
- Create: `frontend/src/lib/image-enhance.ts`

- [ ] **Step 1: Create the enhancement module**

All operations use OpenCV.js which must already be loaded (via `getCV()`).

```typescript
// frontend/src/lib/image-enhance.ts

import { getCV } from "./opencv-loader";

/**
 * Full enhancement pipeline: white balance → shadow removal → CLAHE.
 * Input and output are both HTMLCanvasElement.
 */
export function enhanceDocument(canvas: HTMLCanvasElement): HTMLCanvasElement {
  const cv = getCV();
  const src = cv.imread(canvas);

  autoWhiteBalance(cv, src);
  removeShadows(cv, src);
  applyCLAHE(cv, src);

  const out = document.createElement("canvas");
  cv.imshow(out, src);
  src.delete();
  return out;
}

/**
 * Gray-world white balance: scale each channel so its mean equals
 * the global mean across all channels.
 */
function autoWhiteBalance(cv: any, mat: any): void {
  const channels = new cv.MatVector();
  cv.split(mat, channels);

  // Compute per-channel means (only BGR, skip alpha if present)
  const numC = Math.min(channels.size(), 3);
  const means: number[] = [];
  for (let i = 0; i < numC; i++) {
    const scalar = cv.mean(channels.get(i));
    means.push(scalar[0]);
  }
  const globalMean = means.reduce((a, b) => a + b, 0) / numC;

  for (let i = 0; i < numC; i++) {
    const scale = globalMean / (means[i] || 1);
    const ch = channels.get(i);
    ch.convertTo(ch, -1, scale, 0);
  }

  cv.merge(channels, mat);
  channels.delete();
}

/**
 * Shadow removal: estimate illumination with a large Gaussian blur,
 * divide original by the estimate, normalize.
 */
function removeShadows(cv: any, mat: any): void {
  const gray = new cv.Mat();
  cv.cvtColor(mat, gray, cv.COLOR_RGBA2GRAY);

  const blurred = new cv.Mat();
  const ksize = new cv.Size(51, 51);
  cv.GaussianBlur(gray, blurred, ksize, 0);

  // Divide gray by blurred illumination estimate
  const divided = new cv.Mat();
  gray.convertTo(gray, cv.CV_32F);
  blurred.convertTo(blurred, cv.CV_32F);
  cv.divide(gray, blurred, divided);
  cv.normalize(divided, divided, 0, 255, cv.NORM_MINMAX);
  divided.convertTo(divided, cv.CV_8U);

  // Apply the corrected luminance back to the color image
  const lab = new cv.Mat();
  cv.cvtColor(mat, lab, cv.COLOR_RGBA2RGB);
  cv.cvtColor(lab, lab, cv.COLOR_RGB2Lab);
  const labChannels = new cv.MatVector();
  cv.split(lab, labChannels);

  // Replace L channel
  divided.copyTo(labChannels.get(0));
  cv.merge(labChannels, lab);
  cv.cvtColor(lab, mat, cv.COLOR_Lab2RGBA);

  gray.delete();
  blurred.delete();
  divided.delete();
  lab.delete();
  labChannels.delete();
}

/**
 * CLAHE on the L channel in LAB color space.
 */
function applyCLAHE(cv: any, mat: any): void {
  const lab = new cv.Mat();
  cv.cvtColor(mat, lab, cv.COLOR_RGBA2RGB);
  cv.cvtColor(lab, lab, cv.COLOR_RGB2Lab);

  const channels = new cv.MatVector();
  cv.split(lab, channels);

  const clahe = new cv.CLAHE(2.0, new cv.Size(8, 8));
  const dst = new cv.Mat();
  clahe.apply(channels.get(0), dst);
  dst.copyTo(channels.get(0));

  cv.merge(channels, lab);
  cv.cvtColor(lab, mat, cv.COLOR_Lab2RGBA);

  lab.delete();
  channels.delete();
  dst.delete();
  clahe.delete();
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/image-enhance.ts
git commit -m "feat(scanner): add client-side image enhancement pipeline"
```

---

### Task 5: PDF Builder

**Files:**
- Create: `frontend/src/lib/pdf-builder.ts`

- [ ] **Step 1: Install jspdf**

```bash
cd frontend && npm install jspdf
```

- [ ] **Step 2: Create the PDF builder**

```typescript
// frontend/src/lib/pdf-builder.ts

import { jsPDF } from "jspdf";

export interface ScannedPage {
  canvas: HTMLCanvasElement;
  rotation: number; // 0, 90, 180, 270
}

/**
 * Assemble an array of scanned page canvases into a single PDF blob.
 * Each page is sized to A4 with the image fitting the page.
 */
export function buildPDF(pages: ScannedPage[]): Blob {
  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
  const pageW = 210; // A4 width in mm
  const pageH = 297; // A4 height in mm
  const margin = 5;
  const contentW = pageW - margin * 2;
  const contentH = pageH - margin * 2;

  pages.forEach((page, i) => {
    if (i > 0) doc.addPage();

    const rotated = applyRotation(page.canvas, page.rotation);
    const imgData = rotated.toDataURL("image/jpeg", 0.92);

    // Fit image within content area while preserving aspect ratio
    const imgAspect = rotated.width / rotated.height;
    const pageAspect = contentW / contentH;
    let drawW: number, drawH: number;

    if (imgAspect > pageAspect) {
      drawW = contentW;
      drawH = contentW / imgAspect;
    } else {
      drawH = contentH;
      drawW = contentH * imgAspect;
    }

    const x = margin + (contentW - drawW) / 2;
    const y = margin + (contentH - drawH) / 2;
    doc.addImage(imgData, "JPEG", x, y, drawW, drawH);
  });

  return doc.output("blob");
}

function applyRotation(canvas: HTMLCanvasElement, degrees: number): HTMLCanvasElement {
  if (degrees === 0) return canvas;

  const out = document.createElement("canvas");
  const ctx = out.getContext("2d")!;
  const rad = (degrees * Math.PI) / 180;

  if (degrees === 90 || degrees === 270) {
    out.width = canvas.height;
    out.height = canvas.width;
  } else {
    out.width = canvas.width;
    out.height = canvas.height;
  }

  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate(rad);
  ctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
  return out;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/pdf-builder.ts frontend/package.json frontend/package-lock.json
git commit -m "feat(scanner): add PDF builder for multi-page scans"
```

---

### Task 6: Camera Hook

**Files:**
- Create: `frontend/src/lib/useCamera.ts`

- [ ] **Step 1: Create camera lifecycle hook**

```typescript
// frontend/src/lib/useCamera.ts

import { useCallback, useEffect, useRef, useState } from "react";

interface UseCameraResult {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  stream: MediaStream | null;
  error: string | null;
  ready: boolean;
  captureFrame: () => ImageData | null;
  stop: () => void;
}

export function useCamera(): UseCameraResult {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function start() {
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
          },
        });
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        setStream(s);
        if (videoRef.current) {
          videoRef.current.srcObject = s;
          videoRef.current.onloadedmetadata = () => setReady(true);
        }
      } catch (err: any) {
        if (!cancelled) {
          if (err.name === "NotAllowedError") {
            setError("Camera permission denied. Please allow camera access and refresh.");
          } else if (err.name === "NotFoundError") {
            setError("No camera found on this device.");
          } else {
            setError(`Camera error: ${err.message}`);
          }
        }
      }
    }

    start();

    return () => {
      cancelled = true;
      setStream((prev) => {
        prev?.getTracks().forEach((t) => t.stop());
        return null;
      });
      setReady(false);
    };
  }, []);

  const captureFrame = useCallback((): ImageData | null => {
    const video = videoRef.current;
    if (!video || !ready) return null;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  }, [ready]);

  const stop = useCallback(() => {
    stream?.getTracks().forEach((t) => t.stop());
    setStream(null);
    setReady(false);
  }, [stream]);

  return { videoRef, stream, error, ready, captureFrame, stop };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/useCamera.ts
git commit -m "feat(scanner): add useCamera hook for getUserMedia lifecycle"
```

---

### Task 7: Scanner State Machine Hook

**Files:**
- Create: `frontend/src/lib/useScanner.ts`

- [ ] **Step 1: Create the scanner orchestration hook**

This hook manages the scanning state machine: `loading` → `viewfinder` → `reviewing` → `viewfinder` (add page) or `submitting`.

```typescript
// frontend/src/lib/useScanner.ts

import { useCallback, useReducer, useRef } from "react";
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

export interface UseScannerResult {
  state: ScannerState;
  pages: ScannedPage[];
  dispatch: React.Dispatch<ScannerAction>;
  addPage: (canvas: HTMLCanvasElement, rotation: number) => void;
  clearPages: () => void;
}

export function useScanner(): UseScannerResult {
  const [state, dispatch] = useReducer(reducer, { phase: "loading" });
  const pagesRef = useRef<ScannedPage[]>([]);

  const addPage = useCallback((canvas: HTMLCanvasElement, rotation: number) => {
    pagesRef.current = [...pagesRef.current, { canvas, rotation }];
    dispatch({ type: "add-page", rotation });
  }, []);

  const clearPages = useCallback(() => {
    pagesRef.current = [];
  }, []);

  return { state, pages: pagesRef.current, dispatch, addPage, clearPages };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/useScanner.ts
git commit -m "feat(scanner): add scanner state machine hook"
```

---

### Task 8: WebView Warning Component

**Files:**
- Create: `frontend/src/components/scanner/WebViewWarning.tsx`

- [ ] **Step 1: Create the warning component**

```tsx
// frontend/src/components/scanner/WebViewWarning.tsx

import { useNavigate } from "react-router-dom";

export default function WebViewWarning() {
  const navigate = useNavigate();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#f2f4f6] p-6">
      <div className="bg-white rounded-2xl shadow-xl p-8 max-w-sm text-center space-y-4">
        <span className="material-symbols-outlined text-5xl text-[#93000a]">error</span>
        <h2 className="text-xl font-headline font-bold text-primary">Camera Not Available</h2>
        <p className="text-sm text-[#43474c] leading-relaxed">
          This in-app browser doesn't support camera access.
          Please open this page in <strong>Chrome</strong> or <strong>Edge</strong> on your Android device.
        </p>
        <button
          onClick={() => navigate("/")}
          className="w-full py-3 bg-primary text-white rounded-xl font-bold font-headline"
        >
          Go to Dashboard
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/scanner/WebViewWarning.tsx
git commit -m "feat(scanner): add WebView warning component"
```

---

### Task 9: Scanner Nav Component

**Files:**
- Create: `frontend/src/components/scanner/ScannerNav.tsx`

- [ ] **Step 1: Create the minimal scanner nav bar**

```tsx
// frontend/src/components/scanner/ScannerNav.tsx

import { useNavigate } from "react-router-dom";

interface ScannerNavProps {
  pageCount: number;
  onClose: () => void;
}

export default function ScannerNav({ pageCount, onClose }: ScannerNavProps) {
  return (
    <div className="fixed top-0 left-0 right-0 z-40 flex items-center justify-between px-4 h-14 bg-black/60 text-white backdrop-blur-sm">
      <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10 transition-colors">
        <span className="material-symbols-outlined">close</span>
      </button>
      <h1 className="font-headline font-bold text-sm">Document Scanner</h1>
      <div className="flex items-center gap-1.5 bg-white/10 px-3 py-1 rounded-full">
        <span className="material-symbols-outlined text-sm">description</span>
        <span className="text-sm font-bold">{pageCount}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/scanner/ScannerNav.tsx
git commit -m "feat(scanner): add scanner navigation bar"
```

---

### Task 10: Enhancement Toggle Component

**Files:**
- Create: `frontend/src/components/scanner/EnhancementToggle.tsx`

- [ ] **Step 1: Create the before/after toggle**

```tsx
// frontend/src/components/scanner/EnhancementToggle.tsx

import { useState } from "react";

interface EnhancementToggleProps {
  originalCanvas: HTMLCanvasElement;
  enhancedCanvas: HTMLCanvasElement | null;
}

export default function EnhancementToggle({ originalCanvas, enhancedCanvas }: EnhancementToggleProps) {
  const [showEnhanced, setShowEnhanced] = useState(true);
  const displayCanvas = showEnhanced && enhancedCanvas ? enhancedCanvas : originalCanvas;

  return (
    <div className="relative flex-1 flex items-center justify-center bg-black overflow-hidden">
      <img
        src={displayCanvas.toDataURL("image/jpeg", 0.92)}
        alt="Scanned document"
        className="max-w-full max-h-full object-contain"
      />
      {enhancedCanvas && (
        <button
          onClick={() => setShowEnhanced(!showEnhanced)}
          className="absolute top-4 right-4 flex items-center gap-1.5 bg-black/60 text-white px-3 py-1.5 rounded-full text-xs font-bold backdrop-blur-sm"
        >
          <span className="material-symbols-outlined text-sm">
            {showEnhanced ? "auto_fix_high" : "auto_fix_off"}
          </span>
          {showEnhanced ? "Enhanced" : "Original"}
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/scanner/EnhancementToggle.tsx
git commit -m "feat(scanner): add enhancement before/after toggle"
```

---

### Task 11: Camera Viewfinder Component

**Files:**
- Create: `frontend/src/components/scanner/CameraViewfinder.tsx`

- [ ] **Step 1: Create the live viewfinder with boundary overlay**

```tsx
// frontend/src/components/scanner/CameraViewfinder.tsx

import { useCallback, useEffect, useRef, useState } from "react";
import { useCamera } from "@/lib/useCamera";

interface Corners {
  topLeft: { x: number; y: number };
  topRight: { x: number; y: number };
  bottomLeft: { x: number; y: number };
  bottomRight: { x: number; y: number };
}

interface CameraViewfinderProps {
  onCapture: (imageData: ImageData, corners: Corners | null) => void;
  workerRef: React.RefObject<Worker | null>;
}

export default function CameraViewfinder({ onCapture, workerRef }: CameraViewfinderProps) {
  const { videoRef, error, ready } = useCamera();
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const [corners, setCorners] = useState<Corners | null>(null);
  const [documentDetected, setDocumentDetected] = useState(false);
  const frameCount = useRef(0);

  // Listen for corner detection results from worker
  useEffect(() => {
    const worker = workerRef.current;
    if (!worker) return;

    const handler = (e: MessageEvent) => {
      if (e.data.type === "corners") {
        setCorners(e.data.data);
        setDocumentDetected(true);
      } else if (e.data.type === "no-corners") {
        setCorners(null);
        setDocumentDetected(false);
      }
    };

    worker.addEventListener("message", handler);
    return () => worker.removeEventListener("message", handler);
  }, [workerRef]);

  // Send every 4th frame to worker for detection
  useEffect(() => {
    if (!ready) return;
    const video = videoRef.current;
    if (!video) return;

    let animId: number;
    const canvas = document.createElement("canvas");

    const tick = () => {
      frameCount.current++;
      if (frameCount.current % 4 === 0 && video.videoWidth > 0) {
        // Downsample for detection performance
        const scale = 0.4;
        canvas.width = Math.round(video.videoWidth * scale);
        canvas.height = Math.round(video.videoHeight * scale);
        const ctx = canvas.getContext("2d")!;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);

        workerRef.current?.postMessage(
          {
            type: "detect",
            data: {
              imageData: imageData.data.buffer,
              width: canvas.width,
              height: canvas.height,
            },
          },
          [imageData.data.buffer]
        );
      }

      // Draw overlay
      drawOverlay();
      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [ready, videoRef, workerRef]);

  const drawOverlay = useCallback(() => {
    const overlay = overlayRef.current;
    const video = videoRef.current;
    if (!overlay || !video || video.videoWidth === 0) return;

    overlay.width = overlay.clientWidth;
    overlay.height = overlay.clientHeight;
    const ctx = overlay.getContext("2d")!;
    ctx.clearRect(0, 0, overlay.width, overlay.height);

    if (!corners) return;

    // Scale corners from detection resolution to overlay size
    const video$ = video;
    const scale = 0.4;
    const scaleX = overlay.width / (video$.videoWidth * scale);
    const scaleY = overlay.height / (video$.videoHeight * scale);

    const pts = [corners.topLeft, corners.topRight, corners.bottomRight, corners.bottomLeft];

    ctx.beginPath();
    ctx.moveTo(pts[0].x * scaleX, pts[0].y * scaleY);
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(pts[i].x * scaleX, pts[i].y * scaleY);
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(0, 109, 55, 0.15)";
    ctx.fill();
    ctx.strokeStyle = "#006d37";
    ctx.lineWidth = 3;
    ctx.stroke();

    // Corner dots
    for (const pt of pts) {
      ctx.beginPath();
      ctx.arc(pt.x * scaleX, pt.y * scaleY, 6, 0, Math.PI * 2);
      ctx.fillStyle = "#006d37";
      ctx.fill();
      ctx.strokeStyle = "white";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }, [corners, videoRef]);

  const handleCapture = useCallback(() => {
    const video = videoRef.current;
    if (!video || !ready) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    onCapture(imageData, corners);
  }, [videoRef, ready, corners, onCapture]);

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center bg-black p-6">
        <div className="text-center text-white space-y-3">
          <span className="material-symbols-outlined text-4xl text-[#ffdad6]">videocam_off</span>
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 relative bg-black flex items-center justify-center overflow-hidden">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="absolute inset-0 w-full h-full object-cover"
      />
      <canvas ref={overlayRef} className="absolute inset-0 w-full h-full pointer-events-none" />

      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center bg-black">
          <span className="material-symbols-outlined text-white text-4xl animate-spin">progress_activity</span>
        </div>
      )}

      {/* Capture button */}
      <div className="absolute bottom-8 left-0 right-0 flex justify-center">
        <button
          onClick={handleCapture}
          disabled={!ready}
          className={`w-20 h-20 rounded-full border-4 border-white flex items-center justify-center transition-all active:scale-90 ${
            documentDetected ? "bg-[#006d37]/80" : "bg-white/20"
          }`}
        >
          <div className={`w-14 h-14 rounded-full ${documentDetected ? "bg-[#006d37]" : "bg-white"}`} />
        </button>
      </div>

      {/* Detection status */}
      <div className="absolute bottom-32 left-0 right-0 flex justify-center">
        <span className={`text-xs font-bold px-3 py-1 rounded-full backdrop-blur-sm ${
          documentDetected ? "bg-[#006d37]/80 text-white" : "bg-black/50 text-white/70"
        }`}>
          {documentDetected ? "Document detected" : "Position document in frame"}
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/scanner/CameraViewfinder.tsx
git commit -m "feat(scanner): add camera viewfinder with boundary overlay"
```

---

### Task 12: Capture Review Component

**Files:**
- Create: `frontend/src/components/scanner/CaptureReview.tsx`

- [ ] **Step 1: Create the post-capture review screen**

```tsx
// frontend/src/components/scanner/CaptureReview.tsx

import { useState } from "react";
import EnhancementToggle from "./EnhancementToggle";

interface CaptureReviewProps {
  capturedCanvas: HTMLCanvasElement;
  enhancedCanvas: HTMLCanvasElement | null;
  pageCount: number;
  onRotate: (degrees: number) => void;
  onRetake: () => void;
  onAddPage: (rotation: number) => void;
  onSubmit: (rotation: number) => void;
  onGiveUp: () => void;
}

export default function CaptureReview({
  capturedCanvas,
  enhancedCanvas,
  pageCount,
  onRotate,
  onRetake,
  onAddPage,
  onSubmit,
  onGiveUp,
}: CaptureReviewProps) {
  const [rotation, setRotation] = useState(0);

  const rotate = (dir: number) => {
    const newRot = (rotation + dir + 360) % 360;
    setRotation(newRot);
    onRotate(newRot);
  };

  // Apply rotation to canvases for display
  const rotatedOriginal = rotateCanvas(capturedCanvas, rotation);
  const rotatedEnhanced = enhancedCanvas ? rotateCanvas(enhancedCanvas, rotation) : null;

  return (
    <div className="flex-1 flex flex-col bg-black">
      <EnhancementToggle originalCanvas={rotatedOriginal} enhancedCanvas={rotatedEnhanced} />

      {/* Action buttons */}
      <div className="bg-[#191c1e] p-4 space-y-3">
        {/* Rotation row */}
        <div className="flex justify-center gap-4">
          <button
            onClick={() => rotate(-90)}
            className="p-3 rounded-full bg-white/10 text-white active:bg-white/20"
          >
            <span className="material-symbols-outlined">rotate_left</span>
          </button>
          <button
            onClick={() => rotate(90)}
            className="p-3 rounded-full bg-white/10 text-white active:bg-white/20"
          >
            <span className="material-symbols-outlined">rotate_right</span>
          </button>
        </div>

        {/* Main actions */}
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={onRetake}
            className="py-3 rounded-xl bg-white/10 text-white font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">refresh</span>
            Retake
          </button>
          <button
            onClick={() => onAddPage(rotation)}
            className="py-3 rounded-xl bg-white/10 text-white font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">add_photo_alternate</span>
            Add Page
          </button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={onGiveUp}
            className="py-3 rounded-xl bg-[#93000a]/20 text-[#ffdad6] font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">delete</span>
            Discard All
          </button>
          <button
            onClick={() => onSubmit(rotation)}
            className="py-3 rounded-xl bg-[#006d37] text-white font-bold text-sm flex items-center justify-center gap-1.5"
          >
            <span className="material-symbols-outlined text-sm">send</span>
            Submit ({pageCount + 1} {pageCount + 1 === 1 ? "page" : "pages"})
          </button>
        </div>
      </div>
    </div>
  );
}

function rotateCanvas(canvas: HTMLCanvasElement, degrees: number): HTMLCanvasElement {
  if (degrees === 0) return canvas;
  const out = document.createElement("canvas");
  const ctx = out.getContext("2d")!;
  if (degrees === 90 || degrees === 270) {
    out.width = canvas.height;
    out.height = canvas.width;
  } else {
    out.width = canvas.width;
    out.height = canvas.height;
  }
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate((degrees * Math.PI) / 180);
  ctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
  return out;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/scanner/CaptureReview.tsx
git commit -m "feat(scanner): add capture review with rotate/retake/submit"
```

---

### Task 13: Scanner Page

**Files:**
- Create: `frontend/src/pages/ScannerPage.tsx`
- Install: `jscanify`

- [ ] **Step 1: Install jscanify**

```bash
cd frontend && npm install jscanify
```

- [ ] **Step 2: Create the scanner page**

This is the main orchestrator — it initializes OpenCV + the worker, manages the scan flow, and handles PDF submission.

```tsx
// frontend/src/pages/ScannerPage.tsx

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { isWebView, hasGetUserMedia } from "@/lib/platform";
import { loadOpenCV, getCV } from "@/lib/opencv-loader";
import { enhanceDocument } from "@/lib/image-enhance";
import { buildPDF } from "@/lib/pdf-builder";
import { useScanner } from "@/lib/useScanner";
import ScannerNav from "@/components/scanner/ScannerNav";
import CameraViewfinder from "@/components/scanner/CameraViewfinder";
import CaptureReview from "@/components/scanner/CaptureReview";
import WebViewWarning from "@/components/scanner/WebViewWarning";

export default function ScannerPage() {
  const navigate = useNavigate();
  const { state, pages, dispatch, addPage, clearPages } = useScanner();
  const workerRef = useRef<Worker | null>(null);
  const [loadProgress, setLoadProgress] = useState("Initializing...");

  // Check for WebView / getUserMedia support
  if (isWebView() || !hasGetUserMedia()) {
    return <WebViewWarning />;
  }

  // Initialize OpenCV and Web Worker
  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        setLoadProgress("Loading OpenCV.js...");
        await loadOpenCV();
        if (cancelled) return;

        setLoadProgress("Starting scanner...");
        const worker = new Worker(
          new URL("../workers/corner-detect.worker.ts", import.meta.url),
          { type: "classic" }
        );

        await new Promise<void>((resolve, reject) => {
          worker.onmessage = (e) => {
            if (e.data.type === "ready") resolve();
          };
          worker.onerror = (e) => reject(new Error("Worker failed to start"));
          worker.postMessage({ type: "init" });
        });

        if (cancelled) {
          worker.terminate();
          return;
        }

        workerRef.current = worker;
        dispatch({ type: "loaded" });
      } catch (err: any) {
        if (!cancelled) dispatch({ type: "error", message: err.message });
      }
    }

    init();
    return () => {
      cancelled = true;
      workerRef.current?.terminate();
    };
  }, [dispatch]);

  const handleCapture = useCallback(
    (imageData: ImageData, corners: any) => {
      try {
        const cv = getCV();
        const mat = new cv.Mat(imageData.height, imageData.width, cv.CV_8UC4);
        mat.data.set(imageData.data);

        let resultCanvas: HTMLCanvasElement;

        // If corners were detected, use jscanify to extract and correct perspective
        if (corners) {
          const jscanify = new (window as any).jscanify();
          // Extract with target size matching A4 aspect ratio at high resolution
          const targetW = 2480; // A4 at 300 DPI
          const targetH = 3508;
          resultCanvas = jscanify.extractPaper(mat, targetW, targetH);
        } else {
          // No corners — use the full frame
          resultCanvas = document.createElement("canvas");
          cv.imshow(resultCanvas, mat);
        }
        mat.delete();

        // Run enhancement pipeline
        let enhanced: HTMLCanvasElement | null = null;
        try {
          enhanced = enhanceDocument(resultCanvas);
        } catch (err) {
          console.warn("Enhancement failed, using original:", err);
        }

        dispatch({ type: "captured", canvas: resultCanvas, enhanced });
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
        // Include current page in the set
        const reviewState = state as { phase: "reviewing"; capturedCanvas: HTMLCanvasElement; enhancedCanvas: HTMLCanvasElement | null };
        const finalCanvas = reviewState.enhancedCanvas || reviewState.capturedCanvas;
        const allPages = [...pages, { canvas: finalCanvas, rotation: currentRotation }];

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
      const reviewState = state as { phase: "reviewing"; capturedCanvas: HTMLCanvasElement; enhancedCanvas: HTMLCanvasElement | null };
      const finalCanvas = reviewState.enhancedCanvas || reviewState.capturedCanvas;
      addPage(finalCanvas, rotation);
    },
    [state, addPage]
  );

  const handleGiveUp = useCallback(() => {
    clearPages();
    navigate("/");
  }, [clearPages, navigate]);

  const handleClose = useCallback(() => {
    clearPages();
    navigate("/");
  }, [clearPages, navigate]);

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
        <CameraViewfinder onCapture={handleCapture} workerRef={workerRef} />
      )}

      {state.phase === "reviewing" && (
        <CaptureReview
          capturedCanvas={state.capturedCanvas}
          enhancedCanvas={state.enhancedCanvas}
          pageCount={pages.length}
          onRotate={() => {}}
          onRetake={() => dispatch({ type: "retake" })}
          onAddPage={handleAddPage}
          onSubmit={handleSubmit}
          onGiveUp={handleGiveUp}
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
            onClick={() => dispatch({ type: "loaded" })}
            className="px-6 py-3 bg-white/10 rounded-xl font-bold text-sm"
          >
            Try Again
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ScannerPage.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat(scanner): add main scanner page with full scan flow"
```

---

### Task 14: Route Registration and Android Redirect

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Add scanner route and Android redirect to App.tsx**

Add the scanner route import and a redirect component. Add these changes to `frontend/src/App.tsx`:

At the top, add the import:
```typescript
import { isAndroid } from "@/lib/platform";
import ScannerPage from "@/pages/ScannerPage";
```

Modify the `ProtectedRoute` component to redirect Android users to the scanner:
```typescript
function ProtectedRoute({ children, redirectAndroid }: { children: React.ReactNode; redirectAndroid?: boolean }) {
  const { username, loading } = useAuth();
  if (loading) return (
    <div className="flex items-center justify-center h-screen bg-[#f2f4f6]">
      <div className="flex flex-col items-center gap-3">
        <div className="w-10 h-10 bg-primary flex items-center justify-center rounded-xl">
          <span className="material-symbols-outlined text-white" style={{ fontVariationSettings: "'FILL' 1" }}>receipt_long</span>
        </div>
        <p className="text-sm font-medium text-[#43474c]">Loading...</p>
      </div>
    </div>
  );
  if (!username) return <Navigate to="/login" />;
  if (redirectAndroid && isAndroid()) return <Navigate to="/scan" />;
  return <>{children}</>;
}
```

Add the `/scan` route inside the `<Routes>` block:
```tsx
<Route path="/scan" element={<ProtectedRoute><ScannerPage /></ProtectedRoute>} />
```

Update the dashboard route to include the redirect prop:
```tsx
<Route path="/" element={<ProtectedRoute redirectAndroid><AppLayout><DashboardPage /></AppLayout></ProtectedRoute>} />
```

- [ ] **Step 2: Add scan button to mobile bottom nav in Sidebar.tsx**

In `frontend/src/components/Sidebar.tsx`, find the `NAV_ITEMS` array and add the scanner entry:

```typescript
const NAV_ITEMS = [
  { to: "/", icon: "dashboard",    label: "Dashboard",       exact: true },
  { to: "/documents", icon: "folder_open", label: "Documents",  exact: false },
  { to: "/scan",     icon: "document_scanner", label: "Scan",  exact: false },
  { to: "/export",    icon: "ios_share",   label: "Export",     exact: false },
  { to: "/settings",  icon: "settings",    label: "Administration", exact: false },
];
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(scanner): add /scan route with Android redirect and nav entry"
```

---

### Task 15: jscanify Browser Integration

**Files:**
- Modify: `frontend/index.html` — add jscanify script tag (loads after OpenCV)

- [ ] **Step 1: Add jscanify CDN script to index.html**

jscanify expects to find `cv` on the global scope and is designed to be loaded as a classic script. Since we lazy-load OpenCV.js, we also lazy-load jscanify. However, the scanner page needs it available when OpenCV is ready. The cleanest approach is to load it in the `opencv-loader.ts` after OpenCV is ready.

Modify `frontend/src/lib/opencv-loader.ts` — after OpenCV WASM is initialized, also load jscanify:

Replace the `checkReady` block in `loadOpenCV()` with:

```typescript
    script.onload = () => {
      const checkReady = () => {
        const cv = (window as any).cv;
        if (cv && cv.Mat) {
          cvInstance = cv;
          // Load jscanify after OpenCV is ready
          const scanScript = document.createElement("script");
          scanScript.src = "https://cdn.jsdelivr.net/npm/jscanify@1.4.2/dist/jscanify.min.js";
          scanScript.onload = () => resolve(cv);
          scanScript.onerror = () => resolve(cv); // Still usable without jscanify
          document.head.appendChild(scanScript);
        } else if (cv && cv.onRuntimeInitialized !== undefined) {
          cv.onRuntimeInitialized = () => {
            cvInstance = cv;
            const scanScript = document.createElement("script");
            scanScript.src = "https://cdn.jsdelivr.net/npm/jscanify@1.4.2/dist/jscanify.min.js";
            scanScript.onload = () => resolve(cv);
            scanScript.onerror = () => resolve(cv);
            document.head.appendChild(scanScript);
          };
        } else {
          setTimeout(checkReady, 50);
        }
      };
      checkReady();
    };
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/opencv-loader.ts
git commit -m "feat(scanner): load jscanify after OpenCV WASM init"
```

---

### Task 16: Integration Test and Build Verification

- [ ] **Step 1: Verify TypeScript compilation**

```bash
cd frontend && npx tsc -b
```

Expected: No errors.

- [ ] **Step 2: Verify Vite build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds. Check that `opencv.js` is in `dist/opencv/` (copied from `public/`).

- [ ] **Step 3: Verify Docker build**

```bash
docker compose up -d --build
```

Expected: Container starts without errors.

- [ ] **Step 4: Test on Android device**

1. Open `http://your-nas-ip:8484` on Android Chrome
2. Verify: Redirected to `/scan` instead of dashboard
3. Verify: Loading screen shows while OpenCV loads
4. Verify: Camera viewfinder appears with live feed
5. Verify: Document boundary is highlighted when a document is in frame
6. Verify: Capture button works, shows review screen
7. Verify: Enhancement toggle switches between original/enhanced
8. Verify: Rotate buttons work
9. Verify: "Add Page" returns to viewfinder with page count = 1
10. Verify: "Submit" creates PDF, uploads, and navigates to /documents
11. Verify: The uploaded document appears in the document list

- [ ] **Step 5: Test on desktop browser**

1. Open `http://your-nas-ip:8484` on desktop
2. Verify: Dashboard loads as usual (no redirect to scanner)
3. Verify: "Scan" nav item is visible in sidebar
4. Verify: Clicking "Scan" opens the scanner

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat(scanner): mobile document scanner with boundary detection, enhancement, and PDF upload"
```

---

Plan complete and saved to `docs/superpowers/plans/2026-03-31-mobile-scanner.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?