import { request } from "@playwright/test";

/**
 * Readiness gate. Polls `GET /api/health` until it answers `{"status":"ok"}`.
 *
 * Never a fixed sleep: the container's cold start varies from ~1s on a warm
 * image to ~20s on a cold one, and a sleep long enough for the slow case wastes
 * that time on every fast one.
 */
const READY_TIMEOUT_MS = Number(process.env.READY_TIMEOUT_MS ?? 120_000);
const POLL_INTERVAL_MS = 500;

export default async function globalSetup(): Promise<void> {
  const baseURL = process.env.BASE_URL ?? "http://127.0.0.1:8010";
  const context = await request.newContext({ baseURL, timeout: 5_000 });
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastFailure = "no attempt made";
  let healthy = false;

  try {
    while (Date.now() < deadline) {
      try {
        const response = await context.get("/api/health");
        if (response.ok()) {
          const body = (await response.json()) as { status?: string };
          if (body.status === "ok") {
            // Healthy. Whatever happens next is a verdict, not a reason to
            // keep polling, so break out before checking the static export --
            // throwing from inside this try lands in the catch below, which
            // files it as `lastFailure` and retries. That turned an intended
            // fast-fail into a silent 120s wait ending in a misleading
            // "timed out waiting for /api/health" when health was fine all
            // along.
            healthy = true;
            break;
          }
          lastFailure = `health returned ${JSON.stringify(body)}`;
        } else {
          lastFailure = `health returned HTTP ${response.status()}`;
        }
      } catch (error) {
        lastFailure = error instanceof Error ? error.message : String(error);
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }

    if (healthy) {
      // The UI is served from the same origin. If the static export is missing
      // the app still answers /api/health, and every UI spec would fail on a
      // blank page instead of naming the real cause.
      const index = await context.get("/");
      if (!index.ok()) {
        throw new Error(
          `GET / returned ${index.status()} - the container is up but is not serving the ` +
            `frontend. Check that the Next.js export was copied to /app/static.`,
        );
      }
      process.stdout.write(`[global-setup] ${baseURL} is ready\n`);
      return;
    }

    throw new Error(
      `Timed out after ${READY_TIMEOUT_MS}ms waiting for ${baseURL}/api/health. ` +
        `Last failure: ${lastFailure}. ` +
        `Start the app with: docker compose -f test/docker-compose.test.yml up --build -d app`,
    );
  } finally {
    await context.dispose();
  }
}
