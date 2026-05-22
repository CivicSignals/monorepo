import { defineConfig } from "vitest/config";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

// Vitest config for @civicsignals/web.
// Uses node environment for data/logic tests (no React rendering needed for current tests).
// Uses fileURLToPath + import.meta.url instead of __dirname (package is type:module/ESM).
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export default defineConfig({
  // Automatic JSX runtime so component tests (.tsx) need no explicit React
  // import; the per-file `// @vitest-environment jsdom` pragma opts those into a
  // DOM. Data/logic tests keep the lighter node default below.
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
  test: {
    // Default to node for data/logic tests; component tests opt into jsdom via a
    // per-file `// @vitest-environment jsdom` directive.
    environment: "node",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
});
