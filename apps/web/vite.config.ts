import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

const here = path.dirname(fileURLToPath(import.meta.url));

// The API and the web app share one origin in production. In development and in the end-to-end
// tests the dev and preview servers forward /api to the API, so the browser never makes a
// cross-origin request and the API needs no CORS middleware.
const api = process.env.RESEARCH_API_URL ?? "http://127.0.0.1:8090";
const proxy = { "/api": { target: api, changeOrigin: false } };

// KaTeX lists each font as woff2, woff and ttf. Every browser the build targets (Vite's default
// target is newer than any browser without woff2) reads woff2, so the other two are never
// fetched: leave them out and the build is about 0.9 MB smaller.
const katexWoff2Only: Plugin = {
  name: "katex-woff2-only",
  enforce: "pre",
  transform(code, id) {
    if (!id.endsWith("/katex/dist/katex.min.css")) return null;
    return code.replace(/,url\([^)]*\.(?:woff|ttf)\) format\("(?:woff|truetype)"\)/g, "");
  },
};

export default defineConfig({
  plugins: [katexWoff2Only, react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(here, "src") } },
  build: {
    // research-api answers with `font-src 'self'`, which blocks a `data:` font. Vite inlines files
    // under 4 KiB, so one small KaTeX font became a data: URI and logged a CSP error on every page.
    // Fonts are always files; so are the Qupi layers, whose 1x and 2x versions must not both end up
    // inlined in the page's script. Anything else keeps Vite's default limit.
    assetsInlineLimit: (file) =>
      /\.(?:woff2?|ttf|otf)$/i.test(file) || /shared\/pet\/qupi\/.*\.webp$/.test(file)
        ? false
        : undefined,
  },
  server: { proxy },
  preview: { proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
        "src/shared/api/schema.d.ts",
        "src/shared/ui/**",
        "src/main.tsx",
      ],
      thresholds: { lines: 80 },
    },
  },
});
