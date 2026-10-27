const API_BASE = import.meta.env.DEV ? `http://localhost:${import.meta.env.VITE_API_PORT || "8484"}/api` : "/api";

function parseFilename(disposition: string | null): string {
  const fallback = "receiptory_export.zip";
  if (!disposition) return fallback;
  // Prefer RFC 5987 filename* (UTF-8, may contain non-ASCII names).
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      /* fall through */
    }
  }
  const ascii = disposition.match(/filename="?([^";]+)"?/i);
  return ascii ? ascii[1] : fallback;
}

/**
 * What the owner is told after an export, from the headers the server sends.
 * X-Export-Omitted: documents with no readable file on disk (missing, or
 * none could be opened); they are in the spreadsheet only and stay
 * unstamped, so the next "since last export" retries them. X-Export-Unprocessed: documents delivered before processing
 * finished, as their original file with blank fields; they are stamped
 * (owner, 2026-10-22), so they will not come again. X-Export-Excel-Failed:
 * metadata.xlsx is missing from the zip (metadata.csv has the same rows).
 */
export function exportWarning(
  omittedHeader: string | null,
  unprocessedHeader: string | null = null,
  excelFailedHeader: string | null = null,
): string | null {
  const count = (h: string | null) => {
    const n = Number.parseInt(h ?? "", 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  };
  const omitted = count(omittedHeader);
  const unprocessed = count(unprocessedHeader);
  const parts: string[] = [];
  if (omitted) {
    parts.push(
      omitted === 1
        ? "1 document had no readable file on disk and is only in the spreadsheet."
        : `${omitted} documents had no readable file on disk and are only in the spreadsheet.`,
    );
  }
  if (unprocessed) {
    parts.push(
      unprocessed === 1
        ? "1 document was not processed yet and went out as its original file, without extracted fields; it will not be sent again."
        : `${unprocessed} documents were not processed yet and went out as their original files, without extracted fields; they will not be sent again.`,
    );
  }
  if (excelFailedHeader === "1") parts.push("The Excel sheet could not be written; metadata.csv has every row.");
  return parts.length ? parts.join(" ") : null;
}

/**
 * FastAPI's `detail` is a string for HTTPException but an ARRAY of
 * `{loc, msg, type}` objects for 422 validation errors. Passing the array
 * straight to `new Error()` yields the useless literal "[object Object]", so
 * flatten it to the messages a human can act on.
 */
function detailToMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return "";
}

/**
 * What request() throws for a 401, after sending the user to /login. Callers
 * that must tell an expired session from other failures check the class, not
 * the message: a 500 whose detail happens to read "Unauthorized" is not one.
 */
export class UnauthorizedError extends Error {
  constructor() {
    super("Unauthorized");
    this.name = "UnauthorizedError";
  }
}

/** What request() throws for any other non-2xx response. */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** What downloadOriginal throws when the document's original is not in storage. */
export class MissingOriginalError extends Error {
  constructor() {
    super("The original file is missing from storage");
    this.name = "MissingOriginalError";
  }
}

async function request<T>(path: string, options: RequestInit & { skipAuthRedirect?: boolean } = {}): Promise<T> {
  const { skipAuthRedirect, ...fetchOptions } = options;
  const headers: Record<string, string> = { ...fetchOptions.headers as Record<string, string> };
  if (fetchOptions.body) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers,
    ...fetchOptions,
  });

  if (res.status === 401) {
    if (!skipAuthRedirect && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new UnauthorizedError();
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(detailToMessage(error.detail) || "Request failed", res.status);
  }

  if (res.headers.get("content-type")?.includes("application/json")) {
    return res.json();
  }
  return res as unknown as T;
}

function originalFileUrl(id: number | string): string {
  return `${API_BASE}/documents/${encodeURIComponent(String(id))}/file/original`;
}

// How long downloadOriginal waits on its HEAD check before downloading
// anyway, so a stalled request cannot hold the button pending.
const SESSION_CHECK_TIMEOUT_MS = 10_000;

/**
 * Start a download without navigating. A temporary <a download> turns a failed
 * response (a 404 for a missing original, a 500) into a failed download; a
 * navigation would replace the app with the raw JSON error body. Under
 * `npm run dev` the API is another origin, where browsers ignore `download`
 * and navigate anyway; production serves /api from the page's own origin.
 */
function triggerDownload(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: async (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${API_BASE}/upload`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) {
      // Match request()'s error extraction. Uploads fail for reasons the user
      // can act on (unsupported format, size limits), and a bare "Upload
      // failed" throws that information away.
      //
      // NOTE: a duplicate is NOT an error here — backend/api/upload.py returns
      // HTTP 200 with {"documents": [], "duplicates": [...]}. Callers that care
      // must inspect the success payload; see ScannerPage.handleSubmit.
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(detailToMessage(error.detail) || "Upload failed");
    }
    return res.json();
  },
  exportDocs: async (body: unknown): Promise<{ blob: Blob; filename: string; warning: string | null }> => {
    const res = await fetch(`${API_BASE}/export`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("Export failed");
    return {
      blob: await res.blob(),
      filename: parseFilename(res.headers.get("Content-Disposition")),
      warning: exportWarning(
        res.headers.get("X-Export-Omitted"),
        res.headers.get("X-Export-Unprocessed"),
        res.headers.get("X-Export-Excel-Failed"),
      ),
    };
  },
  uploadScannerTestFrame: async (
    blob: Blob,
    metadata: {
      width: number;
      height: number;
      detector_name?: string;
      corners_at_capture_json?: string;
      notes?: string;
    },
  ) => {
    const form = new FormData();
    form.append("file", blob, "frame.jpg");
    form.append("width", String(metadata.width));
    form.append("height", String(metadata.height));
    if (metadata.detector_name) form.append("detector_name", metadata.detector_name);
    if (metadata.corners_at_capture_json) form.append("corners_at_capture_json", metadata.corners_at_capture_json);
    if (metadata.notes) form.append("notes", metadata.notes);
    const res = await fetch(`${API_BASE}/scanner/test-frames`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) throw new Error("Frame upload failed");
    return res.json();
  },
  listScannerTestFrames: () => request<{ frames: ScannerTestFrame[] }>("/scanner/test-frames"),
  originalFileUrl,
  // The browser fetches the file itself (the session cookie rides along and the
  // server's Content-Disposition: attachment names it), so the download works
  // on the phone without blob handling. A download cannot see a 401 or a 404,
  // though: both would just show a failed download in the browser's tray. So
  // the file is asked for with HEAD through request() first, which sends a 401
  // to /login like every other call; a 404 becomes MissingOriginalError for the
  // page to show. Any other failure of the check still starts the download, so
  // the browser reports the real problem.
  downloadOriginal: async (id: number | string) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SESSION_CHECK_TIMEOUT_MS);
    try {
      await request(`/documents/${encodeURIComponent(String(id))}/file/original`, {
        method: "HEAD",
        signal: controller.signal,
      });
    } catch (e) {
      if (e instanceof UnauthorizedError) throw e;
      if (e instanceof ApiError && e.status === 404) throw new MissingOriginalError();
      // Anything else (a 500, a timeout, offline): try the download anyway;
      // the browser reports its own failure.
    } finally {
      clearTimeout(timer);
    }
    triggerDownload(originalFileUrl(id));
  },
  scannerTestFrameImageUrl: (id: number) => `${API_BASE}/scanner/test-frames/${id}/image`,
  patchScannerTestFrame: (id: number, body: { ground_truth_json?: string; notes?: string }) =>
    request<{ updated: number }>(`/scanner/test-frames/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteScannerTestFrame: (id: number) =>
    request<{ deleted: number }>(`/scanner/test-frames/${id}`, { method: "DELETE" }),
  getScannerActiveConfig: () =>
    request<{ detector: string; params: Record<string, unknown> }>("/scanner/active-config"),
  putScannerActiveConfig: (body: { detector: string; params: Record<string, unknown> }) =>
    request<{ message: string }>("/scanner/active-config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};

export interface ScannerTestFrame {
  id: number;
  frame_path: string;
  captured_at: string;
  width: number;
  height: number;
  detector_name: string | null;
  corners_at_capture_json: string | null;
  ground_truth_json: string | null;
  notes: string | null;
}
