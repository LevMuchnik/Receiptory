import { describe, it, expect, beforeEach, vi } from "vitest";

/**
 * The init-latch regression suite (defect 8).
 *
 * An earlier version of `initScanner` assigned the module-level `scanner`
 * BEFORE awaiting `initialize()`. One failed init therefore left a half-built
 * instance behind, `if (scanner) return` short-circuited every later call, and
 * `initPromise` was never cleared on rejection — so a single transient WASM
 * failure latched permanently and the only escape was the "Try Again" button's
 * `terminateScanner()`. The viewfinder's error badge would report a state that
 * nothing could clear.
 *
 * These tests drive the module against a fake Scanic. No DOM: `opencv-loader`
 * touches `document` only inside `detectCorners` / `extractAndEnhance`, which
 * are deliberately NOT exercised here (they need canvas). Everything the latch
 * fix lives in — `initScanner`, `getScanner`, `terminateScanner` — is reachable
 * with the constructor mocked out.
 */

/** Constructed instances, in order, so tests can assert retry actually retried. */
const constructed: FakeScanner[] = [];
/** What the NEXT `initialize()` call should do. Set per test. */
let initBehaviour: () => Promise<void> = async () => {};

class FakeScanner {
  initializeCalls = 0;
  options: unknown;
  // A plain assignment, not a `public` parameter property: this project builds
  // with `erasableSyntaxOnly`, which rejects parameter properties outright.
  constructor(options: unknown) {
    this.options = options;
    constructed.push(this);
  }
  async initialize(): Promise<void> {
    this.initializeCalls++;
    return initBehaviour();
  }
}

vi.mock("scanic", () => ({ Scanner: FakeScanner }));

// Imported after the mock is registered; vi.mock is hoisted so this is safe.
const { initScanner, getScanner, terminateScanner } = await import("./opencv-loader");

beforeEach(() => {
  // terminateScanner() clears BOTH module globals, which is exactly the state a
  // fresh page load starts in — no vi.resetModules() gymnastics needed.
  terminateScanner();
  constructed.length = 0;
  initBehaviour = async () => {};
});

describe("initScanner — success", () => {
  it("publishes the scanner only after initialize() resolves", async () => {
    let resolveInit: () => void = () => {};
    initBehaviour = () => new Promise<void>((r) => { resolveInit = r; });

    const inFlight = initScanner();
    // Mid-flight: the instance exists but must NOT be reachable yet. Publishing
    // early is the whole defect.
    expect(constructed).toHaveLength(1);
    expect(() => getScanner()).toThrow(/not initialized/i);

    resolveInit();
    await inFlight;
    expect(getScanner()).toBe(constructed[0]);
  });

  it("is a no-op once initialized — no second Scanner is built", async () => {
    await initScanner();
    await initScanner();
    await initScanner();
    expect(constructed).toHaveLength(1);
  });

  it("shares one in-flight promise across concurrent callers", async () => {
    // Three simultaneous callers (the page mounts, the lab mounts, a retry
    // fires) must not each spin up their own WASM instance.
    initBehaviour = () => new Promise<void>((r) => setTimeout(r, 0));
    await Promise.all([initScanner(), initScanner(), initScanner()]);
    expect(constructed).toHaveLength(1);
    expect(constructed[0].initializeCalls).toBe(1);
  });
});

describe("initScanner — failure does not latch", () => {
  it("propagates the rejection (classical-detector's catch depends on it)", async () => {
    initBehaviour = async () => { throw new Error("wasm boom"); };
    await expect(initScanner()).rejects.toThrow("wasm boom");
  });

  it("leaves NO half-built scanner behind after a failed init", async () => {
    initBehaviour = async () => { throw new Error("wasm boom"); };
    await expect(initScanner()).rejects.toThrow();
    // The defect: `scanner` was assigned before the await, so this used to
    // hand back a non-functional instance instead of throwing.
    expect(() => getScanner()).toThrow(/not initialized/i);
  });

  it("RETRIES after a failure and succeeds — the latch fix", async () => {
    initBehaviour = async () => { throw new Error("transient"); };
    await expect(initScanner()).rejects.toThrow("transient");

    initBehaviour = async () => {};
    await initScanner();

    // A second Scanner was genuinely constructed: the failed initPromise was
    // dropped rather than being returned forever to every later caller.
    expect(constructed).toHaveLength(2);
    expect(getScanner()).toBe(constructed[1]);
  });

  it("rejects every concurrent caller of a failing init, and still allows a retry", async () => {
    initBehaviour = () => new Promise<void>((_, reject) => setTimeout(() => reject(new Error("boom")), 0));
    const results = await Promise.allSettled([initScanner(), initScanner()]);
    expect(results.every((r) => r.status === "rejected")).toBe(true);

    initBehaviour = async () => {};
    await initScanner();
    expect(getScanner()).toBe(constructed[constructed.length - 1]);
  });
});

describe("terminateScanner", () => {
  it("drops the instance so the next init builds a fresh one", async () => {
    await initScanner();
    terminateScanner();
    expect(() => getScanner()).toThrow(/not initialized/i);

    await initScanner();
    expect(constructed).toHaveLength(2);
  });
});
