import { expect, test, type Page } from "@playwright/test";

import {
  Api,
  expectCloseTo,
  expectMoney,
  formatUsd,
  openTerminal,
  positionsRow,
  readPositionRow,
  watchlistRow,
  type ChatResponse,
} from "./helpers";

const TICKER = "NVDA";
const WATCH_TICKER = "PYPL";

/**
 * The assistant, in `LLM_MOCK=true` mode.
 *
 * The exact strings asserted here are the ones `planning/LLM_NOTES.md` §3.3
 * publishes as the mock contract. Prices inside an action `detail` are *not*
 * asserted verbatim — they come from the live fill and differ every run — so
 * those are matched by shape and cross-checked against the action's own data.
 */
async function ask(page: Page, message: string): Promise<ChatResponse> {
  const pending = page.waitForResponse(
    (response) => response.url().endsWith("/api/chat") && response.request().method() === "POST",
  );
  await page.getByTestId("chat-input").fill(message);
  await page.getByTestId("chat-send").click();
  const response = await pending;
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as ChatResponse;
}

const lastAssistantTurn = (page: Page) =>
  page.locator('[data-testid="chat-message"][data-role="assistant"]').last();

test.describe("assistant", () => {
  test.beforeEach(async ({ request }) => {
    const api = new Api(request);
    await api.flatten(TICKER);
    await api.ensureNotWatched(WATCH_TICKER);
  });

  test("answers a portfolio question with the live cash balance", async ({ page, request }) => {
    const api = new Api(request);
    await openTerminal(page);

    await expect(page.getByTestId("chat-panel")).toHaveAttribute("data-collapsed", "false");
    await expect(page.getByTestId("chat-send")).toBeDisabled();

    // The empty state offers starter prompts. Clicking one composes it; it must
    // not send, or the user loses the chance to edit it.
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/api/chat") && request.method() === "POST") posts.push(request.url());
    });
    await expect(page.getByTestId("chat-empty")).toBeVisible();
    const suggestions = page.getByTestId("chat-suggestion");
    await expect(suggestions).toHaveCount(3);
    const suggestion = (await suggestions.first().textContent())!.trim();
    await suggestions.first().click();
    await expect(page.getByTestId("chat-input")).toHaveValue(suggestion);
    await expect(page.getByTestId("chat-send")).toBeEnabled();
    expect(posts, "clicking a suggestion must not send it").toEqual([]);

    const before = await api.portfolio();
    const response = await ask(page, "How am I doing?");

    // LLM_NOTES §3.3: the fallback quotes the position count and cash, and
    // deliberately omits total value so it can be asserted verbatim.
    const positions = before.positions.length;
    const expected =
      `You are holding ${positions} position${positions === 1 ? "" : "s"} ` +
      `with ${formatUsd(before.cash_balance)} in cash. ` +
      `Ask me to buy or sell a ticker, or to add one to your watchlist.`;
    expect(response.message).toBe(expected);
    expect(response.actions).toEqual([]);

    await expect(page.locator('[data-testid="chat-message"][data-role="user"]').last()).toContainText(
      "How am I doing?",
    );
    await expect(lastAssistantTurn(page)).toContainText(expected);
    await expect(lastAssistantTurn(page).getByTestId("chat-action")).toHaveCount(0);
  });

  test("executes a buy and shows the fill inline", async ({ page, request }) => {
    const api = new Api(request);
    const cashBefore = (await api.portfolio()).cash_balance;

    await openTerminal(page);
    const response = await ask(page, "Buy 5 shares of NVDA");

    // The assistant's own words are fixed by the mock contract.
    expect(response.message).toBe(`Buying 5 ${TICKER} at the market price now.`);

    // Exactly one action, and it is the trade that was asked for.
    expect(response.actions).toHaveLength(1);
    const action = response.actions[0];
    expect(action.type).toBe("trade");
    expect(action.status).toBe("ok");
    expect(action.data).toMatchObject({ ticker: TICKER, side: "buy", quantity: 5 });
    expect(action.detail).toMatch(new RegExp(`^Bought 5 ${TICKER} @ \\$[\\d,]+\\.\\d{2}$`));

    const price = Number(action.data.price);
    const total = Number(action.data.total);
    expectCloseTo(total, price * 5);
    expect(action.detail).toContain(formatUsd(price));

    // The chip renders the backend's wording verbatim, marked ok.
    const chip = lastAssistantTurn(page).getByTestId("chat-action");
    await expect(chip).toHaveCount(1);
    await expect(chip).toHaveAttribute("data-status", "ok");
    await expect(chip).toHaveAttribute("data-type", "trade");
    await expect(chip).toContainText(action.detail);
    await expect(lastAssistantTurn(page)).toContainText(response.message);

    // A trade the assistant placed moves the same money a manual trade would.
    const after = await api.portfolio();
    expectCloseTo(after.cash_balance, cashBefore - total);
    await expectMoney(page, "header-cash", after.cash_balance);

    // ...and the rest of the terminal caught up with it without a reload.
    await expect(positionsRow(page, TICKER)).toBeVisible();
    const row = await readPositionRow(page, TICKER);
    expect(row.quantity).toBe(5);
    expectCloseTo(row.avgCost, price, 0.01);
    await expect(page.locator(`[data-testid="heatmap-cell"][data-ticker="${TICKER}"]`)).toBeVisible();
  });

  test("sells through the assistant", async ({ page, request }) => {
    const api = new Api(request);
    await api.trade(TICKER, 4, "buy");
    const cashBefore = (await api.portfolio()).cash_balance;

    await openTerminal(page);
    const response = await ask(page, `sell 2 ${TICKER}`);

    expect(response.message).toBe(`Selling 2 ${TICKER} at the market price now.`);
    expect(response.actions).toHaveLength(1);
    const action = response.actions[0];
    expect(action.status).toBe("ok");
    expect(action.data).toMatchObject({ ticker: TICKER, side: "sell", quantity: 2 });
    expect(action.detail).toMatch(new RegExp(`^Sold 2 ${TICKER} @ \\$[\\d,]+\\.\\d{2}$`));

    const after = await api.portfolio();
    expectCloseTo(after.cash_balance, cashBefore + Number(action.data.total));
    expect(await api.heldQuantity(TICKER)).toBeCloseTo(2, 9);

    await expectMoney(page, "header-cash", after.cash_balance);
    expect((await readPositionRow(page, TICKER)).quantity).toBe(2);
  });

  test("reports a rejected trade as an error chip and changes nothing", async ({ page, request }) => {
    const api = new Api(request);
    const before = await api.portfolio();

    await openTerminal(page);
    // Failed actions are a 200 with an error action, not an HTTP error —
    // LLM_NOTES §5. `ask` already asserts the 200.
    const response = await ask(page, `buy 1000000 ${TICKER}`);

    expect(response.message).toBe(`Buying 1000000 ${TICKER} at the market price now.`);
    expect(response.actions).toHaveLength(1);
    const action = response.actions[0];
    expect(action.type).toBe("trade");
    expect(action.status).toBe("error");
    expect(action.data).toMatchObject({ ticker: TICKER, side: "buy", quantity: 1_000_000 });
    // The failure is worded by the trade service and passed through untouched.
    const wording = action.detail.match(
      /^Need \$([\d,]+\.\d{2}) but only \$([\d,]+\.\d{2}) available\.$/,
    );
    expect(wording, `unexpected wording: ${action.detail}`).not.toBeNull();
    expectCloseTo(Number(wording![2].replace(/,/g, "")), before.cash_balance);

    const chip = lastAssistantTurn(page).getByTestId("chat-action");
    await expect(chip).toHaveAttribute("data-status", "error");
    await expect(chip).toContainText(action.detail);

    // Nothing was bought.
    const after = await api.portfolio();
    expect(after.cash_balance).toBe(before.cash_balance);
    expect(await api.heldQuantity(TICKER)).toBe(0);
    await expectMoney(page, "header-cash", before.cash_balance);
  });

  test("adds and removes a watchlist ticker on request", async ({ page, request }) => {
    const api = new Api(request);
    await openTerminal(page);
    const countBefore = await page.getByTestId("watchlist-row").count();

    const added = await ask(page, `add ${WATCH_TICKER} to my watchlist`);
    expect(added.message).toBe(`Adding ${WATCH_TICKER} to your watchlist.`);
    expect(added.actions).toHaveLength(1);
    expect(added.actions[0]).toMatchObject({
      type: "watchlist",
      status: "ok",
      detail: `Added ${WATCH_TICKER} to the watchlist`,
    });
    expect(added.actions[0].data).toMatchObject({ ticker: WATCH_TICKER, action: "add" });

    await expect(watchlistRow(page, WATCH_TICKER)).toBeVisible();
    await expect(page.getByTestId("watchlist-row")).toHaveCount(countBefore + 1);
    expect(await api.watchedTickers()).toContain(WATCH_TICKER);

    const removed = await ask(page, `remove ${WATCH_TICKER}`);
    expect(removed.message).toBe(`Removing ${WATCH_TICKER} from your watchlist.`);
    expect(removed.actions[0]).toMatchObject({
      type: "watchlist",
      status: "ok",
      detail: `Removed ${WATCH_TICKER} from the watchlist`,
    });

    await expect(watchlistRow(page, WATCH_TICKER)).toHaveCount(0);
    await expect(page.getByTestId("watchlist-row")).toHaveCount(countBefore);
    expect(await api.watchedTickers()).not.toContain(WATCH_TICKER);
  });

  test("shows a loading state while the reply is pending", async ({ page }) => {
    await openTerminal(page);

    // Hold the response open so the pending state is observable rather than
    // raced against. Mock replies are otherwise near-instant.
    await page.route("**/api/chat", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
      await route.continue();
    });

    await page.getByTestId("chat-input").fill("How am I doing?");
    await page.getByTestId("chat-send").click();

    await expect(page.getByTestId("chat-loading")).toBeVisible();
    await expect(page.getByTestId("chat-input")).toBeDisabled();
    await expect(page.getByTestId("chat-send")).toBeDisabled();

    await expect(page.getByTestId("chat-loading")).toHaveCount(0, { timeout: 20_000 });
    await expect(page.getByTestId("chat-input")).toBeEnabled();
    await page.unroute("**/api/chat");
  });

  test("restores the conversation, actions included, after a reload", async ({ page, request }) => {
    const api = new Api(request);
    await openTerminal(page);

    const response = await ask(page, "Buy 5 shares of NVDA");
    expect(response.actions).toHaveLength(1);
    const detail = response.actions[0].detail;

    await page.reload();
    await openTerminal(page);

    // History is restored from the server, oldest first, with the chips intact.
    await expect(page.getByTestId("chat-empty")).toHaveCount(0);
    const turns = page.getByTestId("chat-message");
    expect(await turns.count()).toBeGreaterThanOrEqual(2);
    await expect(page.locator('[data-testid="chat-message"][data-role="user"]').last()).toContainText(
      "Buy 5 shares of NVDA",
    );
    await expect(lastAssistantTurn(page)).toContainText(response.message);
    await expect(lastAssistantTurn(page).getByTestId("chat-action")).toContainText(detail);

    const history = await (await request.get("/api/chat/history?limit=50")).json();
    const messages = history.messages as { role: string; actions: unknown }[];
    expect(messages.length).toBeGreaterThanOrEqual(2);
    expect(messages[messages.length - 1].role).toBe("assistant");
    // User turns carry a null actions field; assistant turns carry an array.
    expect(messages[messages.length - 2].actions).toBeNull();
    expect(Array.isArray(messages[messages.length - 1].actions)).toBe(true);

    await api.flatten(TICKER);
  });

  test("collapses and reopens without losing the transcript", async ({ page }) => {
    await openTerminal(page);
    const turns = await page.getByTestId("chat-message").count();
    expect(turns).toBeGreaterThan(0);

    await page.getByTestId("chat-toggle").click();
    await expect(page.getByTestId("chat-panel")).toHaveAttribute("data-collapsed", "true");
    await expect(page.getByTestId("chat-messages")).toHaveCount(0);

    await page.getByTestId("chat-toggle").click();
    await expect(page.getByTestId("chat-panel")).toHaveAttribute("data-collapsed", "false");
    await expect(page.getByTestId("chat-message")).toHaveCount(turns);
  });
});
