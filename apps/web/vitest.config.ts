import { defineConfig } from "vitest/config";
import path from "path";

// Vitest config for @civicsignals/web.
// Uses node environment (no jsdom) for pure data/logic tests.
// Browser-level rendering tests can be added once @testing-library is pinned.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
