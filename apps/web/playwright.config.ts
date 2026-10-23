import { defineConfig } from "@playwright/test";

const apiPort = 8091; // not 8090, so a developer's own API keeps running
const webPort = 4173;
const channel = process.env.E2E_CHANNEL; // e.g. "chrome" to use an installed Chrome instead of Playwright's Chromium

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  // Traces and screenshots stay off: pages show corpus text once the screens exist.
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    // No transitions: a screenshot-free test has no use for a fade. The axe helper also waits for
    // running animations, so the scan does not depend on this setting.
    reducedMotion: "reduce",
    ...(channel ? { channel } : {}),
  },
  projects: [{ name: "chromium" }],
  webServer: [
    {
      // The real HTTP layer over fake ports. /health and /status are the API's own in every test;
      // real-api.spec.ts and circuits.spec.ts also sign in here, with the seeded test accounts.
      command: `uv run --frozen research-api serve --fake --port ${apiPort}`,
      cwd: "../..",
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      // Never reused: the fake server keeps its accounts until it stops, and real-api.spec.ts
      // changes a password that only a fresh server still has as the temporary one.
      reuseExistingServer: false,
    },
    {
      // The production bundle, with /api forwarded to the API above.
      command: `pnpm exec vite build && pnpm exec vite preview --host 127.0.0.1 --port ${webPort} --strictPort`,
      env: { RESEARCH_API_URL: `http://127.0.0.1:${apiPort}` },
      url: `http://127.0.0.1:${webPort}`,
      // Never reused either: a preview left by another worktree would be tested in place of this
      // build, and --strictPort already refuses a taken port, so this says so up front.
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
