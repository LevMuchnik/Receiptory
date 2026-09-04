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
