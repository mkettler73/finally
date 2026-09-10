import { expect, test, type Page } from "@playwright/test";

import {
  openTerminal,
  readWatchlistPrices,
  waitForLiveStream,
  waitForPriceMovement,
} from "./helpers";

/**
 * SSE resilience.
 *
 * `EventSource` reconnects on its own, honouring the server's `retry: 1000`, so
 * there is no reconnection code in the frontend to test — what is tested is
 * that the terminal reports an outage honestly, holds its last prices rather
 * than inventing ticks, and recovers **without a reload** when the stream comes
 * back.
 *
 * The outage is staged by intercepting the stream request rather than by
 * `context.setOffline()`: Chromium's offline emulation does not tear down an
 * SSE response that is already streaming (measured — frames kept arriving),
 * so it cannot simulate this failure at all.
 */

/**
 * A single well-formed frame, in the wire shape of API_CONTRACT §2.
 *
 * The timestamp must be *just behind* wall clock. The price chart feeds every
 * tick to lightweight-charts, which throws `Cannot update oldest data` if a
 * point arrives older than the last one plotted — so a frame stamped in the
 * future would poison the chart and take the page down as soon as real ticks
 * resumed. That says something about the app (see the report), but here it
 * would only be the test shooting itself in the foot.
 */
const INJECTED_PRICE = 190.5;
const injectedFrame = (): string =>
  `retry: 1000\n\n` +
  `data: ${JSON.stringify({
    AAPL: {
      ticker: "AAPL",
      price: INJECTED_PRICE,
      previous_price: 190.4,
      timestamp: Date.now() / 1000 - 1,
      change: 0.1,
      change_percent: 0.0525,
      direction: "up",
    },
  })}\n\n`;

/**
 * Stage a stream that connects, delivers one frame, and then dies — the shape
 * of a backend restart or a dropped connection. Every retry after that is
 * refused, so the page stays visibly disconnected until `unroute`.
 */
async function stageStreamOutage(page: Page): Promise<void> {
  let served = 0;
  await page.route("**/api/stream/prices", async (route) => {
    served += 1;
    if (served === 1) {
      // Fulfilling with a complete body means the response *ends*, which is
      // exactly what the browser sees when a server hangs up mid-stream.
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
        body: injectedFrame(),
      });
      return;
    }
    await route.abort("connectionfailed");
  });
}

test.describe("price stream", () => {
  test("reports a dropped stream and recovers without a reload", async ({ page }) => {
    await stageStreamOutage(page);
    await page.goto("/");
    await expect(page.getByTestId("terminal-root")).toBeVisible();

    // The one frame we served arrives and is rendered — proof the connection
    // was live before it dropped, and that the frame is what drives the cell.
    const aapl = page.locator('[data-testid="watchlist-price"][data-ticker="AAPL"]');
    await expect(aapl).toHaveText("190.50");

    // Then the stream dies, and the terminal says so rather than showing a
    // green dot over frozen data.
    await expect(page.getByTestId("connection-status")).not.toHaveAttribute("data-state", "live", {
      timeout: 30_000,
    });
    await expect(page.getByTestId("connection-status")).toHaveText(/Reconnecting|Disconnected/);

    // Prices hold their last value while it is down. A terminal that keeps
    // animating through an outage is worse than one that freezes.
    const frozen = await readWatchlistPrices(page);
    expect(frozen.AAPL).toBe("190.50");
    await page.waitForTimeout(2_000);
    expect(await readWatchlistPrices(page)).toEqual(frozen);

    // --- the stream comes back ---------------------------------------------
    await page.unroute("**/api/stream/prices");

    // No reload here on purpose: recovery must come from EventSource's own
    // retry, which is the entire reconnection strategy.
    await waitForLiveStream(page);
    await expect(page.getByTestId("connection-status")).toHaveText(/Live/);

    // Recovery means data, not a green dot: real prices replace the stale one
    // and start moving again.
    await expect(aapl).not.toHaveText("190.50", { timeout: 30_000 });
    const moved = await waitForPriceMovement(page, 30_000);
    expect(moved.length).toBeGreaterThan(0);
  });

  test("keeps the account readable while the stream is down", async ({ page }) => {
    await openTerminal(page);
    const cash = (await page.getByTestId("header-cash").textContent())!;
    expect(cash).toMatch(/\$[\d,]+\.\d{2}/);

    await stageStreamOutage(page);
    await page.reload();
    await expect(page.getByTestId("connection-status")).not.toHaveAttribute("data-state", "live", {
      timeout: 30_000,
    });

    // Cash and the positions table come from REST, not from the stream, so an
    // outage must leave them intact rather than blanking them to an em dash.
    await expect(page.getByTestId("header-cash")).toHaveText(cash);
    await expect(page.getByTestId("header-cash")).not.toHaveText(/—/);
    await expect(page.getByTestId("header-total-value")).not.toHaveText(/—/);
    await expect(page.getByTestId("watchlist-row")).not.toHaveCount(0);

    await page.unroute("**/api/stream/prices");
    await waitForLiveStream(page);
  });

  test("re-establishes the stream after a reload", async ({ page }) => {
    await openTerminal(page);
    await waitForPriceMovement(page);

    await page.reload();

    await expect(page.getByTestId("terminal-root")).toBeVisible();
    await waitForLiveStream(page);
    await waitForPriceMovement(page);
  });
});
