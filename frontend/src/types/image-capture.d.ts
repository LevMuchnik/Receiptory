// Minimal ImageCapture declaration. The API ships in Chromium/Android but is
// not in this project's TS lib.dom, and is absent entirely on iOS Safari and
// Firefox — always feature-detect (`"ImageCapture" in window`) before use.
interface ImageCapture {
  takePhoto(photoSettings?: Record<string, unknown>): Promise<Blob>;
  grabFrame(): Promise<ImageBitmap>;
}

declare const ImageCapture: {
  prototype: ImageCapture;
  new (track: MediaStreamTrack): ImageCapture;
};
