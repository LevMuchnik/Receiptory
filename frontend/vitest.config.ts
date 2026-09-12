import { defineConfig } from "vitest/config";
import path from "node:path";

/**
 * Pure-logic test config: node environment, NO jsdom.
 *
 * This is deliberate. The scanner's risky code is pure maths — the temporal
 * smoother's release branches, coordinate-space conversions, quad geometry.
 * None of it needs a DOM. The canvas helpers in `canvas-utils.ts` DO need one
 * (ImageData, drawImage), and they are deliberately left untested: covering
 * them means jsdom plus the native `canvas` package, which drags cairo/pango
 * build deps into the Docker image to gain coverage on ~12 branchless lines.
 *
 * If a test here ever needs `document`, it is testing the wrong thing.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
