import { expect, test } from "@playwright/test";

import {
  Api,
  DEFAULT_TICKERS,
  STARTING_CASH,
  formatUsd,
  openTerminal,
  parseNumber,
  readWatchlistPrices,
  waitForPriceMovement,
  waitForSparklinePoints,
  watchlistRow,
} from "./helpers";

/**
 * First launch, on a database that has never been traded against.
 *
 * This is the only spec that depends on pristine state, which is why it runs
 * first and why `docker-compose.test.yml` gives the app a volume that is
 * destroyed between runs. If it fails on cash, check that the run started with
 * `docker compose -f test/docker-compose.test.yml down -v`.
 */
test.describe("fresh start", () => {
  test("seeds ten tickers, $10,000 in cash and an empty book", async ({ page, request }) => {
    const api = new Api(request);
    await openTerminal(page);

    // --- the watchlist is the seeded ten, in seeded order -------------------
    const rows = page.getByTestId("watchlist-row");
    await expect(rows).toHaveCount(DEFAULT_TICKERS.length);
    const rendered = await rows.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-ticker")),
    );
    expect(rendered).toEqual([...DEFAULT_TICKERS]);

    // --- the account reads exactly $10,000, not merely "some number" --------
    await expect(page.getByTestId("header-cash")).toContainText(formatUsd(STARTING_CASH));
    await expect(page.getByTestId("header-total-value")).toContainText(formatUsd(STARTING_CASH));
    await expect(page.getByTestId("header-return")).toHaveAttribute("data-direction", "flat");
    await expect(page.getByTestId("header-return")).toContainText("0.00%");
    await expect(page.getByTestId("header-unrealized")).toContainText("$0.00");

    // The server must agree with the header — a UI showing $10,000 over a
    // database holding something else is the bug this pairing catches.
    const portfolio = await api.portfolio();
    expect(portfolio.cash_balance).toBe(STARTING_CASH);
    expect(portfolio.starting_cash).toBe(STARTING_CASH);
    expect(portfolio.positions).toEqual([]);
    expect(portfolio.total_value).toBe(STARTING_CASH);

    // --- an empty book renders its empty states, not a zero row ------------
    await expect(page.getByTestId("positions-empty")).toBeVisible();
    await expect(page.getByTestId("heatmap-empty")).toBeVisible();
    await expect(page.getByTestId("heatmap-legend")).toHaveCount(0);
    await expect(page.getByTestId("positions-row")).toHaveCount(0);
  });

  test("streams a live price for every ticker and moves them", async ({ page }) => {
    await openTerminal(page);

    // Every ticker must carry a real price, not the em dash placeholder.
    await expect(async () => {
      const prices = await readWatchlistPrices(page);
      expect(Object.keys(prices)).toHaveLength(DEFAULT_TICKERS.length);
      for (const ticker of DEFAULT_TICKERS) {
        expect(prices[ticker], `${ticker} price cell`).toMatch(/^[\d,]+\.\d{2}$/);
        expect(parseNumber(prices[ticker]), `${ticker} price`).toBeGreaterThan(0);
      }
    }).toPass({ timeout: 30_000 });

    // ...and the prices must actually move. A static snapshot would satisfy the
    // check above while the stream was dead.
    const moved = await waitForPriceMovement(page);
    expect(moved.length).toBeGreaterThan(0);

    await expect(page.getByTestId("connection-status")).toHaveText(/Live/);
  });

  test("accumulates sparklines and charts the default selection", async ({ page }) => {
    await openTerminal(page);

    // The default selection is the head of the watchlist.
    await expect(watchlistRow(page, "AAPL")).toHaveAttribute("data-selected", "true");
    await expect(page.locator('[data-testid="watchlist-row"][data-selected="true"]')).toHaveCount(1);
    await expect(page.getByTestId("price-chart")).toContainText("AAPL");

    // Sparklines start empty and fill in from the stream.
    const sparkline = watchlistRow(page, "AAPL").getByTestId("watchlist-sparkline");
    await expect(sparkline).toBeVisible();
    await waitForSparklinePoints(page, "AAPL", 2);

    // Two accumulated ticks is the point at which the price chart can draw a
    // line, so the placeholder must be gone by then.
    await expect(page.getByTestId("price-chart-empty")).toHaveCount(0);
    await expect(page.getByTestId("price-chart-canvas")).toBeVisible();

    // The chart's headline price and the watchlist cell are fed by the same
    // frame, so they must agree. Read both in one DOM pass, or a tick landing
    // between two reads makes them disagree for honest reasons.
    await expect(async () => {
      const pair = await page.evaluate(() => ({
        chart: document.querySelector('[data-testid="price-chart-last"]')?.textContent?.trim() ?? "",
        row:
          document
            .querySelector('[data-testid="watchlist-price"][data-ticker="AAPL"]')
            ?.textContent?.trim() ?? "",
      }));
      expect(pair.chart).toBe(pair.row);
    }).toPass({ timeout: 15_000 });
  });

  test("clicking a ticker moves the chart to it", async ({ page }) => {
    await openTerminal(page);

    await watchlistRow(page, "TSLA").click();

    await expect(watchlistRow(page, "TSLA")).toHaveAttribute("data-selected", "true");
    await expect(watchlistRow(page, "AAPL")).toHaveAttribute("data-selected", "false");
    await expect(page.getByTestId("price-chart")).toContainText("TSLA");

    // The trade bar follows the selection until it is edited by hand.
    await expect(page.getByTestId("trade-ticker")).toHaveValue("TSLA");

    await expect(async () => {
      const pair = await page.evaluate(() => ({
        chart: document.querySelector('[data-testid="price-chart-last"]')?.textContent?.trim() ?? "",
        row:
          document
            .querySelector('[data-testid="watchlist-price"][data-ticker="TSLA"]')
            ?.textContent?.trim() ?? "",
      }));
      expect(pair.chart).toBe(pair.row);
    }).toPass({ timeout: 15_000 });
  });

  test("the P&L chart has a seeded snapshot to draw", async ({ page, request }) => {
    const api = new Api(request);

    // API_CONTRACT §3: history always holds at least the seed snapshot, so the
    // chart is never empty on a fresh install.
    const snapshots = await api.history();
    expect(snapshots.length).toBeGreaterThanOrEqual(1);
    expect(snapshots[0].total_value).toBeGreaterThan(0);

    // Oldest first, so the chart plots left to right without re-sorting.
    const times = snapshots.map((snapshot) => Date.parse(snapshot.recorded_at));
    expect(times).toEqual([...times].sort((a, b) => a - b));

    await openTerminal(page);
    await expect(page.getByTestId("pnl-chart-canvas")).toBeVisible();
    await expect(page.getByTestId("pnl-chart-empty")).toHaveCount(0);
  });
});
