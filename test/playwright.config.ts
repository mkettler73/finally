import { defineConfig, devices } from "@playwright/test";

/**
 * The suite runs against a *running container*, never against `next dev`: the
 * whole point is to exercise the artefact that ships, static export and all.
 *
 * `BASE_URL` points at it. Inside `docker-compose.test.yml` that is
 * `http://app:8000` on the compose network; from a developer's machine it is
 * whatever host port the container is published on (8010 by default, because
 * 8000 is frequently taken by something else).
 */
const baseURL = process.env.BASE_URL ?? "http://127.0.0.1:8010";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./global-setup.ts",

  /*
   * Deliberately serial, single worker, no retries.
   *
   * The app is single-user by design: one SQLite file, one cash balance, one
   * watchlist. Parallel workers would trade against each other's balance and
   * every assertion about cash would become a lie. Retries are off for the same
   * reason — a test that half-executed a trade cannot be replayed cleanly, and
   * a retry would paper over exactly the flakiness we want reported.
   */
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,

  timeout: 90_000,
  expect: { timeout: 15_000 },

  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }], ["json", { outputFile: "results/results.json" }]]
    : [["list"], ["html", { open: "never" }]],

  outputDir: "results/artifacts",

  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    // A wide viewport: the layout is desktop-first and the heatmap only draws
    // labels once a cell clears 42x24 px.
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
