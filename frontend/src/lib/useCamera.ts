import { useCallback, useEffect, useRef, useState } from "react";

export interface CapturedImage {
  imageData: ImageData;
  /**
   * "photo"  — full-resolution still from ImageCapture.takePhoto(); corners
   *            from the live viewfinder do NOT apply (different resolution /
   *            field of view), so the caller must re-detect on this image.
   * "video"  — a frame grabbed from the preview stream; viewfinder corners
   *            apply after scaling by the detection scale.
   */
  source: "photo" | "video";
}

interface UseCameraResult {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  stream: MediaStream | null;
  error: string | null;
  ready: boolean;
  capturePhoto: () => Promise<CapturedImage | null>;
  stop: () => void;
}

// If takePhoto() hasn't resolved by this point, fall back to a video frame.
// Some devices never resolve the promise; others are just slow.
const TAKE_PHOTO_TIMEOUT_MS = 2000;

// Clamp the captured still's long edge. A full-sensor takePhoto() can be
// 12-108MP; drawing that to a canvas and reading it back (repeatedly, down the
// pipeline) either exhausts mobile memory or silently exceeds the browser
// canvas-area cap (~16.7M px on iOS), which makes getImageData return a blank
// buffer — a black page uploaded with no error. 4096 (≈12.6MP at 4:3) stays
// under that cap and the common 4096px GPU texture limit, preserves a full
// ~12MP still intact, and always exceeds a 4K preview so the photo path can
// never return less than the video-frame fallback.
const MAX_CAPTURE_DIM = 4096;

// Last-resort readiness fallback: if neither loadedmetadata nor loadeddata
// fires (autoplay blocked with deferred metadata on some browsers), force ready
// once frames are actually flowing so the viewfinder can't latch on the black
// loading overlay forever (issue #4).
const READY_TIMEOUT_MS = 2500;

// If frames still haven't arrived by this point the stream failed to start
// (bad constraints, a camera the OS won't hand over, a lens that never
// produces). Surface a retriable error instead of leaving the user on an
// infinite black loading overlay.
const STUCK_TIMEOUT_MS = 7000;

export function useCamera(): UseCameraResult {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // Effect A — acquire the stream once. The PREVIEW only needs to be reliable
  // enough to show the viewfinder and run corner detection; the full-resolution
  // capture comes from ImageCapture.takePhoto(), not this stream. Requesting an
  // extreme/near-square resolution (e.g. ideal 9999x9999) made getUserMedia hang
  // or return a track that never produced frames on some multi-lens Android
  // devices (Galaxy S26 Ultra), leaving `ready` stuck false. Binding to the
  // <video> element happens in Effect B so a null videoRef at resolve time is
  // retried on the next render instead of being silently dropped (the
  // black-screen race, issue #4).
  //
  // Constraint-fallback ladder: multi-lens Android devices frequently reject a
  // fully-specified request with NotReadableError ("Couldn't start video
  // source") or OverconstrainedError — the physical camera the OS maps
  // "environment" onto can't initialize at the requested resolution/framerate.
  // Retry with progressively looser constraints so we get SOME working rear (or
  // any) camera rather than failing outright. `width/height` are `ideal` (not
  // exact) so a satisfiable request is never over-constrained; the ladder exists
  // for the harder failures where even ideal hints wedge the driver.
  useEffect(() => {
    let cancelled = false;

    async function start() {
      const attempts: MediaStreamConstraints[] = [
        { video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } } },
        { video: { facingMode: { ideal: "environment" } } },
        { video: true },
      ];

      let lastErr: any = null;
      for (const constraints of attempts) {
        if (cancelled) return;
        try {
          const s = await navigator.mediaDevices.getUserMedia(constraints);
          if (cancelled) {
            s.getTracks().forEach((t) => t.stop());
            return;
          }
          setStream(s);
          return;
        } catch (err: any) {
          lastErr = err;
          // Permission and no-camera are terminal — looser constraints won't
          // help, so stop laddering and report immediately.
          if (err.name === "NotAllowedError" || err.name === "NotFoundError") break;
        }
      }

      if (cancelled) return;
      if (lastErr?.name === "NotAllowedError") {
        setError("Camera permission denied. Please allow camera access and refresh.");
      } else if (lastErr?.name === "NotFoundError") {
        setError("No camera found on this device.");
      } else if (lastErr?.name === "NotReadableError") {
        setError(
          "Couldn't start the camera. Another app may be using it — close other camera apps, then close and reopen the scanner.",
        );
      } else {
        setError(`Camera error: ${lastErr?.message ?? "unknown"}`);
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

  // Effect B — bind the stream to the video element and track readiness.
  // Runs after every render where `stream` changed, so videoRef is reliably
  // attached (the <video> is rendered unconditionally). Handles the metadata
  // race two ways: read readyState directly for the case where metadata is
  // already loaded (the event would never fire again), and listen for both
  // loadedmetadata and loadeddata for the case where it hasn't.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !stream) return;

    video.srcObject = stream;

    // Re-gate readiness for this stream. Harmless on first bind (already false);
    // on a re-bind it prevents `ready` staying stale-true across the new
    // stream's metadata-load gap.
    setReady(false);

    const onReady = () => setReady(true);
    if (video.readyState >= 1 /* HAVE_METADATA */) {
      setReady(true);
    } else {
      video.addEventListener("loadedmetadata", onReady);
      video.addEventListener("loadeddata", onReady);
    }

    // autoPlay is advisory on mobile — start playback explicitly.
    video.play().catch(() => {});

    const readyTimer = setTimeout(() => {
      if (video.videoWidth > 0) setReady(true);
    }, READY_TIMEOUT_MS);

    // Read videoWidth fresh at fire time (not stale `ready` state): if the
    // stream produced frames it's >0 and we stay silent; if it's still 0 the
    // camera never started, so show an actionable error.
    const stuckTimer = setTimeout(() => {
      if (video.videoWidth === 0) {
        setError("Camera didn't start. Close and reopen the scanner, or reload the page.");
      }
    }, STUCK_TIMEOUT_MS);

    return () => {
      clearTimeout(readyTimer);
      clearTimeout(stuckTimer);
      video.removeEventListener("loadedmetadata", onReady);
      video.removeEventListener("loadeddata", onReady);
    };
  }, [stream]);

  // Grab a frame from the live preview stream (capped at the preview
  // resolution). Used as the fallback when a full-resolution still is
  // unavailable.
  const captureVideoFrame = useCallback((): ImageData | null => {
    const video = videoRef.current;
    if (!video || !ready) return null;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);
    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  }, [ready]);

  // Capture the highest-resolution image the device can provide.
  //
  // Primary path: ImageCapture.takePhoto() returns a full-sensor still, which
  // triggers a dedicated autofocus/exposure pass (typically sharper than the
  // preview). We only trust it if it's genuinely larger than the preview frame
  // — some devices return an upscaled preview from takePhoto().
  //
  // Fallback (iOS, Firefox, timeout, failure, or a faked still): a max-res
  // video frame. The worst case here equals a stream-only implementation.
  const capturePhoto = useCallback(async (): Promise<CapturedImage | null> => {
    const video = videoRef.current;
    if (!video || !ready) return null;

    if (stream && "ImageCapture" in window) {
      const track = stream.getVideoTracks()[0];
      if (track) {
        let timeoutId: ReturnType<typeof setTimeout> | undefined;
        try {
          const capture = new ImageCapture(track);
          const blob = await Promise.race([
            capture.takePhoto(),
            new Promise<never>((_, reject) => {
              timeoutId = setTimeout(() => reject(new Error("takePhoto timeout")), TAKE_PHOTO_TIMEOUT_MS);
            }),
          ]);
          const bitmap = await createImageBitmap(blob);
          try {
            // Compare the long edge, not width — the still may come back in a
            // different orientation than the preview (portrait sensor vs
            // landscape stream), and a width-only test would reject a genuinely
            // higher-resolution photo.
            const photoLongEdge = Math.max(bitmap.width, bitmap.height);
            const previewLongEdge = Math.max(video.videoWidth, video.videoHeight);
            // Gate on what we can actually DELIVER (after the clamp), not the
            // raw sensor size — otherwise a huge still that clamps below the
            // preview would be accepted and hand back less than the video-frame
            // fallback would.
            const deliverableLongEdge = Math.min(photoLongEdge, MAX_CAPTURE_DIM);
            if (deliverableLongEdge > previewLongEdge) {
              // Downscale to a bounded long edge so a huge sensor still can't
              // OOM the tab or overflow the canvas-area cap (see MAX_CAPTURE_DIM).
              const clampScale = Math.min(1, MAX_CAPTURE_DIM / photoLongEdge);
              const w = Math.round(bitmap.width * clampScale);
              const h = Math.round(bitmap.height * clampScale);
              const canvas = document.createElement("canvas");
              canvas.width = w;
              canvas.height = h;
              const ctx = canvas.getContext("2d")!;
              ctx.imageSmoothingEnabled = true;
              ctx.imageSmoothingQuality = "high";
              ctx.drawImage(bitmap, 0, 0, w, h);
              const imageData = ctx.getImageData(0, 0, w, h);
              return { imageData, source: "photo" };
            }
          } finally {
            bitmap.close();
          }
        } catch {
          // Fall through to the video-frame path.
        } finally {
          // takePhoto() usually wins the race; clear the loser so we don't leak
          // a live timer (and a late rejection) for up to TAKE_PHOTO_TIMEOUT_MS.
          clearTimeout(timeoutId);
        }
      }
    }

    const frame = captureVideoFrame();
    return frame ? { imageData: frame, source: "video" } : null;
  }, [ready, stream, captureVideoFrame]);

  const stop = useCallback(() => {
    stream?.getTracks().forEach((t) => t.stop());
    setStream(null);
    setReady(false);
  }, [stream]);

  return { videoRef, stream, error, ready, capturePhoto, stop };
}
