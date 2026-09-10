import { expect, test, type Page } from "@playwright/test";

import {
  Api,
  expectCloseTo,
  expectMoney,
  formatPrice,
  formatQuantity,
  formatUsd,
  openTerminal,
  parseMoney,
  positionsRow,
  readCash,
  readPositionRow,
  type Trade,
} from "./helpers";

const TICKER = "NVDA";

/** Place a market order on the trade bar and return the fill the server reported. */
async function placeOrder(
  page: Page,
  side: "buy" | "sell",
  ticker: string,
  quantity: number,
): Promise<Trade> {
  await page.getByTestId("trade-ticker").fill(ticker);
  await page.getByTestId("trade-quantity").fill(String(quantity));

  const pending = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/portfolio/trade") && response.request().method() === "POST",
  );
  await page.getByTestId(side === "buy" ? "trade-buy" : "trade-sell").click();
  const response = await pending;
  expect(response.status(), await response.text()).toBe(200);
  const body = (await response.json()) as { trade: Trade };
  return body.trade;
}

test.describe("trading", () => {
  test.beforeEach(async ({ request }) => {
    // Start from a flat book in this ticker so quantity assertions are exact.
    await new Api(request).flatten(TICKER);
  });

  test("a buy takes exactly quantity x price out of cash", async ({ page, request }) => {
    const api = new Api(request);
    const cashBefore = (await api.portfolio()).cash_balance;

    await openTerminal(page);
    expectCloseTo(await readCash(page), cashBefore);

    const trade = await placeOrder(page, "buy", TICKER, 3);

    // The fill itself is internally consistent.
    expect(trade.ticker).toBe(TICKER);
    expect(trade.side).toBe("buy");
    expect(trade.quantity).toBe(3);
    expect(trade.price).toBeGreaterThan(0);
    expectCloseTo(trade.total, trade.quantity * trade.price);

    // The money assertion the whole feature rests on: cash fell by the traded
    // amount, to the cent — not merely "cash went down".
    const after = await api.portfolio();
    expectCloseTo(after.cash_balance, cashBefore - trade.quantity * trade.price);
    await expectMoney(page, "header-cash", after.cash_balance);

    // The fill confirmation quotes the same numbers back.
    const status = page.getByTestId("trade-status");
    await expect(status).toBeVisible();
    await expect(status).toHaveText(
      `Bought 3 ${TICKER} at ${formatPrice(trade.price)} — ${formatUsd(trade.total)}`,
    );
    await expect(page.getByTestId("trade-error")).toHaveCount(0);
    await expect(page.getByTestId("trade-quantity")).toHaveValue("");

    // The position appears with the quantity and cost basis just paid.
    const row = await readPositionRow(page, TICKER);
    expect(row.quantity).toBe(3);
    expectCloseTo(row.avgCost, trade.price);
    await expect(page.getByTestId("positions-empty")).toHaveCount(0);

    // Unrealised P&L must be the marked value less what it cost — the panel
    // header total has to agree with the row, or the two disagree by a cent and
    // the terminal looks broken.
    await expect(async () => {
      const marked = await readPositionRow(page, TICKER);
      // The row's own cells must be arithmetically consistent. The tolerance is
      // the rounding slack in three 2dp cells, not a licence to be wrong.
      expectCloseTo(marked.pnl, marked.quantity * (marked.last - marked.avgCost), 0.05);
      expectCloseTo(marked.value, marked.quantity * marked.last, 0.05);

      // ...and the panel-header total must be that same figure, signed.
      const total = await page.getByTestId("positions-total-pnl").textContent();
      expectCloseTo(parseMoney(total), marked.pnl, 0.01);
      expect(await page.getByTestId("positions-pnl").getAttribute("data-direction")).toBe(
        marked.pnl > 0 ? "up" : marked.pnl < 0 ? "down" : "flat",
      );
    }).toPass({ timeout: 15_000 });
  });

  test("a partial sell returns cash and leaves the average cost alone", async ({ page, request }) => {
    const api = new Api(request);
    const opening = await api.trade(TICKER, 4, "buy");
    const avgCost = opening.portfolio.positions.find((p) => p.ticker === TICKER)!.avg_cost;
    const cashBefore = opening.portfolio.cash_balance;

    await openTerminal(page);
    await expect(positionsRow(page, TICKER)).toBeVisible();

    const sale = await placeOrder(page, "sell", TICKER, 1.5);
    expect(sale.side).toBe("sell");
    expect(sale.quantity).toBe(1.5);

    const after = await api.portfolio();
    expectCloseTo(after.cash_balance, cashBefore + sale.quantity * sale.price);
    await expectMoney(page, "header-cash", after.cash_balance);

    const held = after.positions.find((position) => position.ticker === TICKER)!;
    expect(held.quantity).toBeCloseTo(2.5, 9);
    // API_CONTRACT §3: a sell never moves the average cost.
    expectCloseTo(held.avg_cost, avgCost, 1e-6);

    const row = await readPositionRow(page, TICKER);
    expect(row.quantity).toBe(2.5);
    expect(await positionsRow(page, TICKER).locator("td").nth(0).textContent()).toBe(
      formatQuantity(2.5),
    );
    expectCloseTo(row.avgCost, avgCost);
  });

  test("selling out closes the position and empties the book", async ({ page, request }) => {
    const api = new Api(request);
    const opening = await api.trade(TICKER, 2, "buy");
    const cashBefore = opening.portfolio.cash_balance;

    await openTerminal(page);
    await expect(positionsRow(page, TICKER)).toBeVisible();

    const sale = await placeOrder(page, "sell", TICKER, 2);

    const after = await api.portfolio();
    expectCloseTo(after.cash_balance, cashBefore + sale.quantity * sale.price);
    expect(after.positions.find((position) => position.ticker === TICKER)).toBeUndefined();

    await expect(positionsRow(page, TICKER)).toHaveCount(0);
    await expect(page.getByTestId("positions-empty")).toBeVisible();
    await expect(page.getByTestId("heatmap-empty")).toBeVisible();
    await expectMoney(page, "header-cash", after.cash_balance);

    // With no positions, the account is all cash again.
    await expectMoney(page, "header-total-value", after.cash_balance);
  });

  test("an unaffordable buy is refused in the server's own words", async ({ page, request }) => {
    const api = new Api(request);
    const cashBefore = (await api.portfolio()).cash_balance;

    await openTerminal(page);

    const pending = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/portfolio/trade") && response.request().method() === "POST",
    );
    await page.getByTestId("trade-ticker").fill(TICKER);
    await page.getByTestId("trade-quantity").fill("1000000");
    await page.getByTestId("trade-buy").click();

    const response = await pending;
    expect(response.status()).toBe(400);
    const body = (await response.json()) as { detail: { code: string; message: string } };
    expect(body.detail.code).toBe("INSUFFICIENT_CASH");
    // The data layer's wording, per API_CONTRACT §5.
    const wording = body.detail.message.match(
      /^Need \$([\d,]+\.\d{2}) but only \$([\d,]+\.\d{2}) available\.$/,
    );
    expect(wording, `unexpected wording: ${body.detail.message}`).not.toBeNull();
    // The "available" figure is the real balance, not a placeholder.
    expectCloseTo(Number(wording![2].replace(/,/g, "")), cashBefore);

    await expect(page.getByTestId("trade-error")).toHaveText(body.detail.message);
    await expect(page.getByTestId("trade-status")).toHaveCount(0);

    // Nothing moved.
    expect((await api.portfolio()).cash_balance).toBe(cashBefore);
    await expectMoney(page, "header-cash", cashBefore);
    await expect(positionsRow(page, TICKER)).toHaveCount(0);
  });

  test("selling shares that are not held is refused", async ({ page, request }) => {
    const api = new Api(request);
    const cashBefore = (await api.portfolio()).cash_balance;
    expect(await api.heldQuantity(TICKER)).toBe(0);

    await openTerminal(page);

    const pending = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/portfolio/trade") && response.request().method() === "POST",
    );
    await page.getByTestId("trade-ticker").fill(TICKER);
    await page.getByTestId("trade-quantity").fill("5");
    await page.getByTestId("trade-sell").click();

    const response = await pending;
    expect(response.status()).toBe(400);
    const body = (await response.json()) as { detail: { code: string; message: string } };
    expect(body.detail.code).toBe("INSUFFICIENT_SHARES");
    expect(body.detail.message).toContain(TICKER);

    await expect(page.getByTestId("trade-error")).toHaveText(body.detail.message);
    expect((await api.portfolio()).cash_balance).toBe(cashBefore);
  });

  test("the trade bar refuses a zero quantity locally", async ({ page }) => {
    await openTerminal(page);

    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/api/portfolio/trade")) posts.push(request.url());
    });

    await page.getByTestId("trade-ticker").fill(TICKER);
    await page.getByTestId("trade-quantity").fill("0");
    await page.getByTestId("trade-buy").click();

    await expect(page.getByTestId("trade-error")).toHaveText("Enter a quantity greater than zero.");
    expect(posts, "an invalid order must not reach the API").toEqual([]);
  });

  test("the order estimate quotes the live price times the quantity", async ({ page }) => {
    await openTerminal(page);
    await page.getByTestId("trade-ticker").fill(TICKER);

    // With no quantity the estimate is the per-share price alone.
    await expect(page.getByTestId("trade-estimate")).toHaveText(/^[\d,]+\.\d{2} a share$/, {
      timeout: 20_000,
    });

    await page.getByTestId("trade-quantity").fill("4");

    // Read the estimate and the price cell together: both come from the same
    // frame, so the arithmetic must hold exactly at any single instant.
    await expect(async () => {
      const { estimate, price } = await page.evaluate((ticker) => ({
        estimate: document.querySelector('[data-testid="trade-estimate"]')?.textContent?.trim() ?? "",
        price:
          document
            .querySelector(`[data-testid="watchlist-price"][data-ticker="${ticker}"]`)
            ?.textContent?.trim() ?? "",
      }), TICKER);

      const match = estimate.match(/^([\d,]+\.\d{2}) a share — \$([\d,]+\.\d{2})$/);
      expect(match, `estimate did not parse: ${estimate}`).not.toBeNull();
      const quoted = Number(match![1].replace(/,/g, ""));
      const total = Number(match![2].replace(/,/g, ""));
      expect(quoted).toBe(Number(price.replace(/,/g, "")));
      expect(total).toBeCloseTo(quoted * 4, 2);
    }).toPass({ timeout: 15_000 });
  });
});
