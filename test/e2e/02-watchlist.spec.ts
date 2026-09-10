import { expect, test } from "@playwright/test";

import {
  Api,
  DEFAULT_TICKERS,
  openTerminal,
  parseNumber,
  watchlistRow,
} from "./helpers";

const NEW_TICKER = "PYPL";

/**
 * Watchlist CRUD through the UI, cross-checked against the server.
 *
 * Every spec here leaves the watchlist as it found it — the default ten — so
 * the specs that follow can rely on it.
 */
test.describe("watchlist", () => {
  test.beforeEach(async ({ request }) => {
    await new Api(request).ensureNotWatched(NEW_TICKER);
  });

  test("adds a ticker, prices it, and removes it again", async ({ page, request }) => {
    const api = new Api(request);
    await openTerminal(page);
    await expect(page.getByTestId("watchlist-row")).toHaveCount(DEFAULT_TICKERS.length);

    // --- add ---------------------------------------------------------------
    await page.getByTestId("watchlist-add-input").fill("pypl");
    // The field uppercases as you type, so the request is normalised before it
    // ever leaves the browser.
    await expect(page.getByTestId("watchlist-add-input")).toHaveValue("PYPL");

    const addResponse = page.waitForResponse(
      (response) => response.url().endsWith("/api/watchlist") && response.request().method() === "POST",
    );
    await page.getByTestId("watchlist-add-submit").click();
    expect((await addResponse).status()).toBe(201);

    const row = watchlistRow(page, NEW_TICKER);
    await expect(row).toBeVisible();
    await expect(page.getByTestId("watchlist-row")).toHaveCount(DEFAULT_TICKERS.length + 1);
    await expect(page.getByTestId("watchlist-add-input")).toHaveValue("");
    await expect(page.getByTestId("watchlist-add-error")).toHaveCount(0);

    // Adding selects it, and the trade bar follows the selection.
    await expect(row).toHaveAttribute("data-selected", "true");
    await expect(page.getByTestId("price-chart")).toContainText(NEW_TICKER);

    // The point of adding a ticker is that it starts streaming: the row must
    // acquire a real price, not sit at the em dash forever.
    const priceCell = page.locator(`[data-testid="watchlist-price"][data-ticker="${NEW_TICKER}"]`);
    await expect(priceCell).toHaveText(/^[\d,]+\.\d{2}$/, { timeout: 30_000 });
    expect(parseNumber(await priceCell.textContent())).toBeGreaterThan(0);

    // The server holds it too, appended last (sorted by added_at ascending).
    const watched = await api.watchedTickers();
    expect(watched).toContain(NEW_TICKER);
    expect(watched[watched.length - 1]).toBe(NEW_TICKER);

    // --- remove ------------------------------------------------------------
    const deleteResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/watchlist/${NEW_TICKER}`) &&
        response.request().method() === "DELETE",
    );
    await row.getByTestId("watchlist-remove").click();
    expect((await deleteResponse).status()).toBe(200);

    await expect(row).toHaveCount(0);
    await expect(page.getByTestId("watchlist-row")).toHaveCount(DEFAULT_TICKERS.length);
    expect(await api.watchedTickers()).not.toContain(NEW_TICKER);
  });

  test("shows the server's own wording when a ticker is already watched", async ({ page, request }) => {
    const api = new Api(request);
    await api.addTicker(NEW_TICKER);
    await openTerminal(page);
    await expect(watchlistRow(page, NEW_TICKER)).toBeVisible();

    const conflict = page.waitForResponse(
      (response) => response.url().endsWith("/api/watchlist") && response.request().method() === "POST",
    );
    await page.getByTestId("watchlist-add-input").fill(NEW_TICKER);
    await page.getByTestId("watchlist-add-submit").click();

    const response = await conflict;
    expect(response.status()).toBe(409);
    const body = (await response.json()) as { detail: { code: string; message: string } };
    expect(body.detail.code).toBe("TICKER_ALREADY_WATCHED");

    // TESTIDS.md: the add error renders `detail.message` verbatim. Asserting
    // "an error appeared" would pass even if the UI showed the wrong one.
    await expect(page.getByTestId("watchlist-add-error")).toHaveText(body.detail.message);
    expect(body.detail.message).toContain(NEW_TICKER);

    // ...and the list did not grow.
    await expect(page.getByTestId("watchlist-row")).toHaveCount(DEFAULT_TICKERS.length + 1);

    await api.removeTicker(NEW_TICKER);
  });

  test("rejects a malformed ticker in the browser, without calling the API", async ({ page }) => {
    await openTerminal(page);

    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/api/watchlist")) {
        posts.push(request.url());
      }
    });

    await page.getByTestId("watchlist-add-input").fill("123");
    await page.getByTestId("watchlist-add-submit").click();

    await expect(page.getByTestId("watchlist-add-error")).toHaveText("Enter a ticker of 1-10 letters.");
    await expect(page.getByTestId("watchlist-row")).toHaveCount(DEFAULT_TICKERS.length);
    expect(posts, "a locally invalid ticker must not reach the API").toEqual([]);
  });

  test("keeps a held ticker priced after it leaves the watchlist", async ({ page, request }) => {
    const api = new Api(request);
    // A position must keep streaming even when its ticker is unwatched, or the
    // portfolio cannot be valued (API_CONTRACT §4).
    await api.addTicker(NEW_TICKER);
    const { trade } = await api.trade(NEW_TICKER, 1, "buy");
    expect(trade.ticker).toBe(NEW_TICKER);

    await openTerminal(page);
    await watchlistRow(page, NEW_TICKER).getByTestId("watchlist-remove").click();
    await expect(watchlistRow(page, NEW_TICKER)).toHaveCount(0);

    // The position survives the removal and is still marked to a live price.
    const positionPrice = page.locator(`[data-testid="positions-price"][data-ticker="${NEW_TICKER}"]`);
    await expect(positionPrice).toBeVisible();

    const before = (await positionPrice.textContent())?.trim();
    await expect(async () => {
      expect((await positionPrice.textContent())?.trim()).not.toBe(before);
    }).toPass({ timeout: 25_000, intervals: [250, 500, 1000] });

    await api.flatten(NEW_TICKER);
  });
});
