import { api } from "@/lib/api";
import type { Quad } from "./detector";

const LONG_EDGE = 1280;
const JPEG_QUALITY = 0.85;

export function uploadTestFrame(
  imageData: ImageData,
  detectorName: string | null,
  corners: Quad | null,
): void {
  // Fire-and-forget. Don't block the scanner flow on upload latency or auth errors.
  encodeJpeg(imageData)
    .then((blob) => {
      if (!blob) return;
      return api.uploadScannerTestFrame(blob, {
        width: imageData.width,
        height: imageData.height,
        detector_name: detectorName ?? undefined,
        corners_at_capture_json: corners ? JSON.stringify(corners) : undefined,
      });
    })
    .catch((err) => {
      console.warn("Test-frame upload failed:", err);
    });
}

async function encodeJpeg(image: ImageData): Promise<Blob | null> {
  const longEdge = Math.max(image.width, image.height);
  const scale = longEdge > LONG_EDGE ? LONG_EDGE / longEdge : 1;
  const w = Math.round(image.width * scale);
  const h = Math.round(image.height * scale);

  const src = document.createElement("canvas");
  src.width = image.width;
  src.height = image.height;
  src.getContext("2d")!.putImageData(image, 0, 0);

  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  out.getContext("2d")!.drawImage(src, 0, 0, w, h);

  return new Promise((resolve) => {
    out.toBlob((b) => resolve(b), "image/jpeg", JPEG_QUALITY);
  });
}
