/**
 * Playwright (5.424.0): presentation mode's browser suites — the rendered
 * overflow check and the multi-user run — against the Vite dev server and an
 * in-memory backend (`e2e/fakeBackend.ts`), so they need no running stack.
 * `npm run e2e` (the gate runs it: scripts/check.sh's "frontend e2e" step).
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT ?? 4317);

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices['Desktop Chrome'],
    viewport: { width: 1600, height: 1000 },
  },
  webServer: {
    command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
