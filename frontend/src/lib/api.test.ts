import { describe, it, expect, vi, afterEach } from "vitest";
import { api } from "./api";

/**
 * `api.upload` error extraction.
 *
 * This used to be `throw new Error("Upload failed")` for every non-2xx, which
 * threw away the one thing the user could act on. Seven call sites surface that
 * message: the scanner's submit toast, the WebView camera-app fallback, the
 * sidebar and both page-level drop zones. The new code mirrors `request()`'s
 * ladder, so it is pinned here — including the shape it does NOT handle.
 *
 * No DOM: `fetch`, `FormData` and `File` are all Node globals, and `api.upload`
 * touches nothing else.
 */

const png = () => new File([new Uint8Array([1, 2, 3])], "receipt.jpg", { type: "image/jpeg" });

function stubFetch(res: Partial<Response> & { json?: () => Promise<unknown> }) {
  const spy = vi.fn().mockResolvedValue(res);
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.upload — success", () => {
  it("returns the parsed body", async () => {
    stubFetch({ ok: true, json: async () => ({ documents: [{ id: 7 }], duplicates: [] }) });
    await expect(api.upload([png()])).resolves.toEqual({ documents: [{ id: 7 }], duplicates: [] });
  });

  it("posts multipart form data with every file under the `files` field", async () => {
    const spy = stubFetch({ ok: true, json: async () => ({}) });
    await api.upload([png(), png()]);
    const body = spy.mock.calls[0][1].body as FormData;
    expect(spy.mock.calls[0][1].method).toBe("POST");
    expect(body.getAll("files")).toHaveLength(2);
  });
});

describe("api.upload — error extraction", () => {
  it("surfaces the backend's `detail` instead of a generic message", async () => {
    stubFetch({
      ok: false,
      statusText: "Bad Request",
      json: async () => ({ detail: "Invalid file type: .exe" }),
    });
    await expect(api.upload([png()])).rejects.toThrow("Invalid file type: .exe");
  });

  it("falls back to the HTTP status text when the body is not JSON", async () => {
    // A proxy 502 or an nginx error page: res.json() rejects and the ladder
    // has to survive it rather than throwing an unhandled parse error.
    stubFetch({
      ok: false,
      statusText: "Bad Gateway",
      json: async () => { throw new SyntaxError("Unexpected token <"); },
    });
    await expect(api.upload([png()])).rejects.toThrow("Bad Gateway");
  });

  it("falls back to 'Upload failed' when the body is JSON with no detail", async () => {
    stubFetch({ ok: false, statusText: "", json: async () => ({}) });
    await expect(api.upload([png()])).rejects.toThrow("Upload failed");
  });

  it("flattens a FastAPI 422 `detail` ARRAY instead of stringifying it", async () => {
    // FastAPI returns validation errors as a list of {loc, msg, type} objects,
    // not a string. Handing that array straight to `new Error()` produced the
    // literal "[object Object]" -- strictly worse than the generic fallback it
    // replaced. detailToMessage flattens it to the messages instead.
    stubFetch({
      ok: false,
      statusText: "Unprocessable Entity",
      json: async () => ({ detail: [{ loc: ["body", "files"], msg: "field required" }] }),
    });
    await expect(api.upload([png()])).rejects.toThrow("field required");
  });

  it("joins multiple 422 validation messages", async () => {
    stubFetch({
      ok: false,
      statusText: "Unprocessable Entity",
      json: async () => ({
        detail: [{ msg: "field required" }, { msg: "value is not a valid integer" }],
      }),
    });
    await expect(api.upload([png()])).rejects.toThrow("field required; value is not a valid integer");
  });

  it("never surfaces the literal [object Object]", async () => {
    // The regression guard proper: whatever shape `detail` arrives in, the
    // message must be something a human can act on.
    for (const detail of [
      [{ msg: "boom" }],
      { code: "x", reason: "y" },
      ["plain string in a list"],
    ]) {
      stubFetch({ ok: false, statusText: "Bad Request", json: async () => ({ detail }) });
      await expect(api.upload([png()])).rejects.not.toThrow("[object Object]");
    }
  });
});

describe("api.originalFileUrl", () => {
  it("points at the original-file download endpoint", () => {
    expect(api.originalFileUrl(42)).toMatch(/\/api\/documents\/42\/file\/original$/);
  });

  it("encodes a route param so it cannot change the path", () => {
    expect(api.originalFileUrl("1/../../x")).toMatch(/\/api\/documents\/1%2F..%2F..%2Fx\/file\/original$/);
  });
});

/**
 * `api.downloadOriginal`: a plain link cannot see a 401, so the session is
 * checked through `request()` first (which sends an expired session to
 * /login like every other call). The download then goes through a temporary
 * `<a download>`, not a navigation: a failed download (404, 500) must not
 * replace the app with a raw JSON page. `window` and `document` here are
 * plain objects, not a DOM.
 */
describe("api.downloadOriginal", () => {
  function stubBrowser() {
    const location = { pathname: "/documents/42", href: "", assign: vi.fn() };
    const anchors: { href: string; download: string; click: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> }[] = [];
    const appendChild = vi.fn();
    vi.stubGlobal("window", { location });
    vi.stubGlobal("document", {
      createElement: vi.fn(() => {
        const a = { href: "", download: "", rel: "", style: {}, click: vi.fn(), remove: vi.fn() };
        anchors.push(a);
        return a;
      }),
      body: { appendChild },
    });
    return { location, anchors, appendChild };
  }

  it("downloads through a temporary link once the session is confirmed", async () => {
    const { location, anchors, appendChild } = stubBrowser();
    const fetchSpy = stubFetch({ ok: true, status: 200, json: async () => ({ username: "admin" }) });
    await api.downloadOriginal(42);
    expect(fetchSpy.mock.calls[0][0]).toMatch(/\/api\/auth\/me$/);
    expect(anchors).toHaveLength(1);
    expect(anchors[0].href).toBe(api.originalFileUrl(42));
    expect(anchors[0].download).toBe("");
    expect(appendChild).toHaveBeenCalledWith(anchors[0]);
    expect(anchors[0].click).toHaveBeenCalledOnce();
    expect(anchors[0].remove).toHaveBeenCalledOnce();
    // never a navigation: a failed download must not replace the app
    expect(location.assign).not.toHaveBeenCalled();
  });

  it("sends an expired session to /login instead of a failed download", async () => {
    const { location, anchors } = stubBrowser();
    stubFetch({ ok: false, status: 401, json: async () => ({ detail: "Not authenticated" }) });
    await expect(api.downloadOriginal(42)).rejects.toThrow("Unauthorized");
    expect(anchors).toHaveLength(0);
    expect(location.href).toBe("/login");
  });

  // Value: protects=a non-401 HTTP failure of /auth/me (e.g. 500) still starts the download; fails_when=the catch is narrowed to network errors (TypeError) so an HTTP error rethrows; why_new=only a rejected fetch was tested; seam=none; no_claim=what the browser shows for the failed download
  it("still starts the download when the session check returns a server error", async () => {
    const { location, anchors } = stubBrowser();
    stubFetch({ ok: false, status: 500, statusText: "Internal Server Error", json: async () => ({ detail: "boom" }) });
    await api.downloadOriginal(42);
    expect(anchors[0].click).toHaveBeenCalledOnce();
    expect(location.href).toBe("");
  });

  it("tells an expired session apart by its status, not by the error text", async () => {
    // a 500 whose detail happens to read "Unauthorized" is not a 401
    const { location, anchors } = stubBrowser();
    stubFetch({ ok: false, status: 500, json: async () => ({ detail: "Unauthorized" }) });
    await api.downloadOriginal(42);
    expect(anchors[0].click).toHaveBeenCalledOnce();
    expect(location.href).toBe("");
  });

  it("still starts the download when the check fails for another reason", async () => {
    const { anchors } = stubBrowser();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await api.downloadOriginal(42);
    expect(anchors[0].click).toHaveBeenCalledOnce();
  });

  it("starts the download anyway when the session check stalls", async () => {
    // A hung /auth/me must not keep the button pending until the tab closes.
    vi.useFakeTimers();
    try {
      const { anchors } = stubBrowser();
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_url: string, init: RequestInit) =>
            new Promise((_resolve, reject) => {
              init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
            }),
        ),
      );
      const done = api.downloadOriginal(42);
      await vi.advanceTimersByTimeAsync(9_999);
      expect(anchors).toHaveLength(0); // still waiting on the check
      await vi.advanceTimersByTimeAsync(1);
      await done;
      expect(anchors[0].click).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });
});
