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
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detailToMessage(error.detail) || "Request failed");
  }

  if (res.headers.get("content-type")?.includes("application/json")) {
    return res.json();
  }
  return res as unknown as T;
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
  exportDocs: async (body: unknown): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(`${API_BASE}/export`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("Export failed");
    return { blob: await res.blob(), filename: parseFilename(res.headers.get("Content-Disposition")) };
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
