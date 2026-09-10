import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/* ------------------------------------------------------------------ *
 * Contract shapes (API_CONTRACT.md §3–§5), narrowed to what we assert.
 * ------------------------------------------------------------------ */

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  cost_basis: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  weight: number;
}

export interface Portfolio {
  cash_balance: number;
  positions: Position[];
  positions_value: number;
  total_value: number;
  total_unrealized_pnl: number;
  total_return_percent: number;
  starting_cash: number;
}

export interface Trade {
  id: string;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  total: number;
  executed_at: string;
}

export interface ChatAction {
  type: "trade" | "watchlist";
  status: "ok" | "error";
  detail: string;
  data: Record<string, unknown>;
}

export interface ChatResponse {
  message: string;
  actions: ChatAction[];
  created_at: string;
}

export interface WatchlistEntry {
  ticker: string;
  price: number | null;
  session_open: number | null;
  session_change_percent: number | null;
  added_at: string;
}

/** The ten tickers `PLAN.md` §7 seeds, in seeded order. */
export const DEFAULT_TICKERS = [
  "AAPL",
  "GOOGL",
  "MSFT",
  "AMZN",
  "TSLA",
  "NVDA",
  "META",
  "JPM",
  "V",
  "NFLX",
] as const;

export const STARTING_CASH = 10_000;

/* ------------------------------------------------------------------ *
 * Formatting — mirrors frontend/src/lib/format.ts exactly.
 *
 * The UI renders 2dp strings from unrounded floats, so an assertion has to
 * compare against the same string the frontend would have produced. Comparing
 * a parsed number against a raw float would fail on the half-cent.
 * ------------------------------------------------------------------ */

const money2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** `1902.5` -> `"$1,902.50"`; negatives take the sign ahead of the symbol. */
export function formatUsd(value: number): string {
  return `${value < 0 ? "-" : ""}$${money2.format(Math.abs(value))}`;
}

/** `190.25` -> `"190.25"` — prices carry no currency symbol in the UI. */
export function formatPrice(value: number): string {
  return money2.format(value);
}

/** Trailing zeros trimmed, as `formatQuantity` does. `3` -> `"3"`, `2.5` -> `"2.5"`. */
export function formatQuantity(value: number): string {
  const fixed = value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return fixed === "" || fixed === "-" ? "0" : fixed;
}

/**
 * Pull the last money figure out of a rendered string.
 *
 * Header cells include their label — `header-cash` reads `"Cash$9,392.50"` —
 * so we take the last `$…` token, and we keep the sign that precedes it.
 */
export function parseMoney(text: string | null): number {
  if (!text) throw new Error("parseMoney: no text");
  const matches = [...text.matchAll(/([+-]?)\$([\d,]+\.\d{2})/g)];
  if (matches.length === 0) throw new Error(`parseMoney: no money figure in ${JSON.stringify(text)}`);
  const [, sign, digits] = matches[matches.length - 1];
  return Number(digits.replace(/,/g, "")) * (sign === "-" ? -1 : 1);
}

/** Bare number cells (prices, quantities): `"1,234.50"` -> `1234.5`. */
export function parseNumber(text: string | null): number {
  if (!text) throw new Error("parseNumber: no text");
  const match = text.replace(/,/g, "").match(/-?\d+(\.\d+)?/);
  if (!match) throw new Error(`parseNumber: no number in ${JSON.stringify(text)}`);
  return Number(match[0]);
}

/* ------------------------------------------------------------------ *
 * Reading the terminal
 * ------------------------------------------------------------------ */

export const testId = (page: Page, id: string): Locator => page.getByTestId(id);

export async function readCash(page: Page): Promise<number> {
  return parseMoney(await page.getByTestId("header-cash").textContent());
}

export async function readTotalValue(page: Page): Promise<number> {
  return parseMoney(await page.getByTestId("header-total-value").textContent());
}

/**
 * Wait until the header has rendered a real figure. Everything money-shaped in
 * the header is an em dash until the first `GET /api/portfolio` resolves, and a
 * test that reads it earlier gets a dash, not a number.
 */
export async function waitForPortfolioLoaded(page: Page): Promise<void> {
  await expect(page.getByTestId("header-cash")).not.toHaveText(/—/, { timeout: 30_000 });
}

/** Wait for the SSE connection indicator to report a live stream. */
export async function waitForLiveStream(page: Page): Promise<void> {
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-state", "live", {
    timeout: 30_000,
  });
}

/**
 * Open the terminal and wait until it is actually usable: mounted, portfolio
 * loaded, stream live, and a default instrument selected.
 */
export async function openTerminal(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("terminal-root")).toBeVisible();
  await waitForPortfolioLoaded(page);
  await waitForLiveStream(page);
  await expect(page.locator('[data-testid="watchlist-row"][data-selected="true"]')).toHaveCount(1);
}

/** Every watchlist price cell, keyed by ticker, read in one atomic DOM pass. */
export async function readWatchlistPrices(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const out: Record<string, string> = {};
    for (const cell of document.querySelectorAll('[data-testid="watchlist-price"]')) {
      const ticker = cell.getAttribute("data-ticker");
      if (ticker) out[ticker] = (cell.textContent ?? "").trim();
    }
    return out;
  });
}

/**
 * Prove the stream is *moving*, not merely present: watch until at least one
 * watchlist price differs from the snapshot we started with.
 *
 * Returns the tickers that moved. Never asserts a particular price — the
 * simulator is stochastic and any exact-price assertion would flake.
 */
export async function waitForPriceMovement(
  page: Page,
  timeoutMs = 20_000,
): Promise<string[]> {
  const before = await readWatchlistPrices(page);
  let moved: string[] = [];
  await expect(async () => {
    const after = await readWatchlistPrices(page);
    moved = Object.keys(after).filter((ticker) => before[ticker] !== after[ticker]);
    expect(moved.length, "no watchlist price changed — is the stream delivering frames?").toBeGreaterThan(0);
  }).toPass({ timeout: timeoutMs, intervals: [250, 250, 500, 500, 1000] });
  return moved;
}

/** Wait until a sparkline has accumulated at least `points` ticks. */
export async function waitForSparklinePoints(page: Page, ticker: string, points: number): Promise<void> {
  await expect(async () => {
    const value = await page
      .locator(`[data-testid="watchlist-row"][data-ticker="${ticker}"] [data-testid="watchlist-sparkline"]`)
      .getAttribute("data-points");
    expect(Number(value ?? 0)).toBeGreaterThanOrEqual(points);
  }).toPass({ timeout: 30_000 });
}

/* ------------------------------------------------------------------ *
 * Talking to the API directly
 *
 * Used for arranging state (a position that must exist before the page loads)
 * and for cross-checking what the UI renders against what the server holds.
 * ------------------------------------------------------------------ */

export class Api {
  constructor(private readonly request: APIRequestContext) {}

  private async json<T>(response: import("@playwright/test").APIResponse, what: string): Promise<T> {
    if (!response.ok()) {
      throw new Error(`${what} failed: HTTP ${response.status()} ${await response.text()}`);
    }
    return (await response.json()) as T;
  }

  async portfolio(): Promise<Portfolio> {
    return this.json(await this.request.get("/api/portfolio"), "GET /api/portfolio");
  }

  async watchlist(): Promise<WatchlistEntry[]> {
    const body = await this.json<{ tickers: WatchlistEntry[] }>(
      await this.request.get("/api/watchlist"),
      "GET /api/watchlist",
    );
    return body.tickers;
  }

  async watchedTickers(): Promise<string[]> {
    return (await this.watchlist()).map((entry) => entry.ticker);
  }

  async history(limit = 500): Promise<{ total_value: number; recorded_at: string }[]> {
    const body = await this.json<{ snapshots: { total_value: number; recorded_at: string }[] }>(
      await this.request.get(`/api/portfolio/history?limit=${limit}`),
      "GET /api/portfolio/history",
    );
    return body.snapshots;
  }

  async trade(ticker: string, quantity: number, side: "buy" | "sell"): Promise<{ trade: Trade; portfolio: Portfolio }> {
    return this.json(
      await this.request.post("/api/portfolio/trade", { data: { ticker, quantity, side } }),
      `POST /api/portfolio/trade ${side} ${quantity} ${ticker}`,
    );
  }

  async addTicker(ticker: string): Promise<void> {
    const response = await this.request.post("/api/watchlist", { data: { ticker } });
    if (!response.ok() && response.status() !== 409) {
      throw new Error(`POST /api/watchlist ${ticker} failed: HTTP ${response.status()}`);
    }
  }

  async removeTicker(ticker: string): Promise<void> {
    const response = await this.request.delete(`/api/watchlist/${ticker}`);
    if (!response.ok() && response.status() !== 404) {
      throw new Error(`DELETE /api/watchlist/${ticker} failed: HTTP ${response.status()}`);
    }
  }

  /** Ensure a ticker is *not* watched, whatever state a previous spec left. */
  async ensureNotWatched(ticker: string): Promise<void> {
    if ((await this.watchedTickers()).includes(ticker)) await this.removeTicker(ticker);
  }

  /** Quantity currently held of a ticker; 0 when there is no position. */
  async heldQuantity(ticker: string): Promise<number> {
    const held = (await this.portfolio()).positions.find((position) => position.ticker === ticker);
    return held?.quantity ?? 0;
  }

  /** Flatten a position so a spec starts from a known book. */
  async flatten(ticker: string): Promise<void> {
    const quantity = await this.heldQuantity(ticker);
    if (quantity > 0) await this.trade(ticker, quantity, "sell");
  }
}

/* ------------------------------------------------------------------ *
 * Assertion helpers
 * ------------------------------------------------------------------ */

/**
 * Wait for a testid to render an exact money string.
 *
 * The header is repriced on every frame, so it can be a tick behind a mutation
 * that just landed; polling for the expected string is the honest way to wait
 * for it without asserting on a moving figure.
 */
export async function expectMoney(page: Page, id: string, expected: number): Promise<void> {
  const wanted = formatUsd(expected);
  await expect(page.getByTestId(id), `${id} should read ${wanted}`).toContainText(wanted, {
    timeout: 20_000,
  });
}

/** Cents-tolerant equality, for float sums that cross a JSON round trip. */
export function expectCloseTo(actual: number, expected: number, tolerance = 0.01): void {
  expect(
    Math.abs(actual - expected),
    `expected ${actual} to be within ${tolerance} of ${expected}`,
  ).toBeLessThanOrEqual(tolerance);
}

export const watchlistRow = (page: Page, ticker: string): Locator =>
  page.locator(`[data-testid="watchlist-row"][data-ticker="${ticker}"]`);

export const positionsRow = (page: Page, ticker: string): Locator =>
  page.locator(`[data-testid="positions-row"][data-ticker="${ticker}"]`);

export const heatmapCell = (page: Page, ticker: string): Locator =>
  page.locator(`[data-testid="heatmap-cell"][data-ticker="${ticker}"]`);

/** The cells of a positions row, in the column order Positions.tsx renders. */
export async function readPositionRow(page: Page, ticker: string): Promise<{
  quantity: number;
  avgCost: number;
  last: number;
  value: number;
  pnl: number;
  pnlDirection: string | null;
  pnlPercent: number;
  pnlPercentDirection: string | null;
}> {
  const row = positionsRow(page, ticker);
  await expect(row).toBeVisible();
  const cells = row.locator("td");
  const [quantity, avgCost, last, value] = await Promise.all([
    cells.nth(0).textContent(),
    cells.nth(1).textContent(),
    cells.nth(2).textContent(),
    cells.nth(3).textContent(),
  ]);
  const pnlCell = row.getByTestId("positions-pnl");
  const pnlPercentCell = row.getByTestId("positions-pnl-percent");
  return {
    quantity: parseNumber(quantity),
    avgCost: parseNumber(avgCost),
    last: parseNumber(last),
    value: parseMoney(value),
    pnl: parseMoney(await pnlCell.textContent()),
    pnlDirection: await pnlCell.getAttribute("data-direction"),
    pnlPercent: parseNumber(await pnlPercentCell.textContent()),
    pnlPercentDirection: await pnlPercentCell.getAttribute("data-direction"),
  };
}
