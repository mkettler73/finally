import { expect, test } from "@playwright/test";

import {
  Api,
  expectCloseTo,
  heatmapCell,
  openTerminal,
  parseMoney,
  watchlistRow,
} from "./helpers";

const BIG = "NVDA";
const SMALL = "AAPL";

/**
 * The two portfolio visualisations: the allocation treemap and the P&L chart.
 *
 * Both are canvases or SVG, so the assertions are on the data attributes the
 * frontend publishes for exactly this purpose (`TESTIDS.md`), cross-checked
 * against the positions table rendered from the same state.
 */
test.describe("portfolio visualisations", () => {
  test.beforeEach(async ({ request }) => {
    const api = new Api(request);
    await api.flatten(BIG);
    await api.flatten(SMALL);
  });

  test.afterEach(async ({ request }) => {
    const api = new Api(request);
    await api.flatten(BIG);
    await api.flatten(SMALL);
  });

  test("the heatmap sizes cells by weight and colours them by P&L", async ({ page, request }) => {
    const api = new Api(request);
    // Two deliberately unequal positions, so "sized by weight" is falsifiable.
    await api.trade(BIG, 6, "buy");
    await api.trade(SMALL, 1, "buy");

    await openTerminal(page);
    await expect(page.getByTestId("heatmap-cell")).toHaveCount(2);
    await expect(page.getByTestId("heatmap-empty")).toHaveCount(0);
    await expect(page.getByTestId("heatmap-legend")).toBeVisible();

    // Read the treemap and the positions table in one pass: both render from
    // the same repriced portfolio, so at any single instant they must agree.
    // Reading them separately would let a price tick land in between and
    // manufacture a disagreement that is not a bug.
    const snapshot = await page.evaluate(() => {
      const cells = [...document.querySelectorAll('[data-testid="heatmap-cell"]')].map((cell) => ({
        ticker: cell.getAttribute("data-ticker") ?? "",
        weight: Number(cell.getAttribute("data-weight")),
        direction: cell.getAttribute("data-pnl-direction"),
        area: (() => {
          const rect = cell.querySelector("rect");
          return rect
            ? Number(rect.getAttribute("width")) * Number(rect.getAttribute("height"))
            : 0;
        })(),
      }));
      const rows = [...document.querySelectorAll('[data-testid="positions-row"]')].map((row) => {
        const tds = row.querySelectorAll("td");
        const text = (node: Element | null | undefined) => (node?.textContent ?? "").trim();
        return {
          ticker: row.getAttribute("data-ticker") ?? "",
          quantity: Number(text(tds[0]).replace(/,/g, "")),
          avgCost: Number(text(tds[1]).replace(/,/g, "")),
          last: Number(text(tds[2]).replace(/,/g, "")),
          value: text(tds[3]),
          pnlDirection: row
            .querySelector('[data-testid="positions-pnl"]')
            ?.getAttribute("data-direction"),
        };
      });
      const totalValue = (
        document.querySelector('[data-testid="header-total-value"]')?.textContent ?? ""
      ).trim();
      return { cells, rows, totalValue };
    });

    const total = parseMoney(snapshot.totalValue);
    expect(snapshot.rows).toHaveLength(2);

    for (const cell of snapshot.cells) {
      const row = snapshot.rows.find((candidate) => candidate.ticker === cell.ticker);
      expect(row, `no positions row for heatmap cell ${cell.ticker}`).toBeDefined();

      // Weight is market value over total portfolio value, as a percentage.
      const marketValue = row!.quantity * row!.last;
      expectCloseTo(cell.weight, (marketValue / total) * 100, 0.05);

      // Colour direction is the sign of the unrealised P&L. It is read off the
      // same float the positions table uses, so those two must always agree.
      expect(cell.direction, `${cell.ticker} colour vs positions table`).toBe(row!.pnlDirection);

      // Against the *rendered* prices the comparison only holds when they are
      // more than a cent apart — below that the 2dp cells cannot tell the sign,
      // and asserting anyway would be a coin flip rather than a test.
      if (Math.abs(row!.last - row!.avgCost) >= 0.02) {
        expect(cell.direction, `${cell.ticker} colour vs last/avg cost`).toBe(
          row!.last > row!.avgCost ? "up" : "down",
        );
      }
    }

    // The heavier position gets the larger rectangle — this is the "sized by
    // weight" claim, and it is the one thing a treemap can get visibly wrong.
    const big = snapshot.cells.find((cell) => cell.ticker === BIG)!;
    const small = snapshot.cells.find((cell) => cell.ticker === SMALL)!;
    expect(big.weight).toBeGreaterThan(small.weight);
    expect(big.area).toBeGreaterThan(small.area);

    // Weights are shares of the whole account, so they sum to the invested
    // share of it — not to 100, because cash is not in the treemap.
    const portfolio = await api.portfolio();
    const invested = (portfolio.positions_value / portfolio.total_value) * 100;
    expectCloseTo(big.weight + small.weight, invested, 0.5);
  });

  test("a heatmap cell reports its holding on hover and selects it on click", async ({
    page,
    request,
  }) => {
    const api = new Api(request);
    await api.trade(BIG, 3, "buy");
    await openTerminal(page);

    const cell = heatmapCell(page, BIG);
    await expect(cell).toBeVisible();

    await cell.hover();
    const tooltip = page.getByTestId("heatmap-tooltip");
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText(BIG);

    // The tooltip's weight is the cell's own weight attribute, rendered.
    const weight = Number(await cell.getAttribute("data-weight"));
    await expect(tooltip).toContainText(`${weight.toFixed(2)}%`);
    await expect(tooltip).toContainText("3");

    await cell.click();
    await expect(watchlistRow(page, BIG)).toHaveAttribute("data-selected", "true");
    await expect(page.getByTestId("price-chart")).toContainText(BIG);
  });

  test("the P&L chart gains a point on every trade", async ({ page, request }) => {
    const api = new Api(request);
    const before = await api.history();
    expect(before.length).toBeGreaterThanOrEqual(1);

    await openTerminal(page);
    await expect(page.getByTestId("pnl-chart-canvas")).toBeVisible();

    const { portfolio } = await api.trade(BIG, 2, "buy");

    const after = await api.history();
    expect(after.length).toBeGreaterThan(before.length);

    // API_CONTRACT §3: a snapshot is written in the trade's own transaction, so
    // the newest point is the portfolio value at the moment of the fill.
    const newest = after[after.length - 1];
    expectCloseTo(newest.total_value, portfolio.total_value, Math.max(1, portfolio.total_value * 0.005));
    expect(Date.parse(newest.recorded_at)).toBeGreaterThanOrEqual(
      Date.parse(before[before.length - 1].recorded_at),
    );

    // Still oldest-first after the append.
    const times = after.map((snapshot) => Date.parse(snapshot.recorded_at));
    expect(times).toEqual([...times].sort((a, b) => a - b));

    await page.reload();
    await openTerminal(page);
    await expect(page.getByTestId("pnl-chart-empty")).toHaveCount(0);
    await expect(page.getByTestId("pnl-chart-canvas").locator("canvas").first()).toBeVisible();
  });
});
