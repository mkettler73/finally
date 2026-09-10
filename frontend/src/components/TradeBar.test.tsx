import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { makeFrame, sampleWatchlist } from "@/lib/fixtures";
import { installFakeApi, installFakeStream, renderTerminal, type FakeApi, type FakeStream } from "@/test/harness";
import { Header } from "./Header";
import { TradeBar } from "./TradeBar";

let stream: FakeStream;
let api: FakeApi;

beforeEach(() => {
  stream = installFakeStream();
  api = installFakeApi({ watchlist: sampleWatchlist.slice(0, 3) });
});

describe("TradeBar", () => {
  it("follows the selected instrument until the field is edited", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));

    await user.clear(screen.getByTestId("trade-ticker"));
    await user.type(screen.getByTestId("trade-ticker"), "tsla");
    expect(screen.getByTestId("trade-ticker")).toHaveValue("TSLA");
  });

  it("estimates the order from the live price", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));

    await stream.emit(makeFrame({ AAPL: 190.25 }));
    await user.type(screen.getByTestId("trade-quantity"), "10");

    expect(screen.getByTestId("trade-estimate")).toHaveTextContent("190.25 a share — $1,902.50");
  });

  it("says so when the ticker has no price yet", async () => {
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));
    expect(screen.getByTestId("trade-estimate")).toHaveTextContent("No price yet");
  });

  it("places a market buy and confirms the fill", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));

    await user.type(screen.getByTestId("trade-quantity"), "10");
    await user.click(screen.getByTestId("trade-buy"));

    expect(await screen.findByTestId("trade-status")).toHaveTextContent(
      "Bought 10 AAPL at 190.25 — $1,902.50",
    );
    expect(api.calls).toContainEqual({
      method: "POST",
      path: "/api/portfolio/trade",
      body: { ticker: "AAPL", quantity: 10, side: "buy" },
    });
    // A filled order clears the quantity so the next one starts clean.
    expect(screen.getByTestId("trade-quantity")).toHaveValue("");
  });

  it("places a sell with the same fields", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));

    await user.type(screen.getByTestId("trade-quantity"), "2.5");
    await user.click(screen.getByTestId("trade-sell"));

    await screen.findByTestId("trade-status");
    expect(api.calls).toContainEqual({
      method: "POST",
      path: "/api/portfolio/trade",
      body: { ticker: "AAPL", quantity: 2.5, side: "sell" },
    });
  });

  it("surfaces the API's error message verbatim", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));

    api.failNext(
      "/api/portfolio/trade",
      "INSUFFICIENT_CASH",
      "Need $1,902.50 but only $500.00 available.",
    );
    await user.type(screen.getByTestId("trade-quantity"), "10");
    await user.click(screen.getByTestId("trade-buy"));

    expect(await screen.findByTestId("trade-error")).toHaveTextContent(
      "Need $1,902.50 but only $500.00 available.",
    );
  });

  it("rejects a non-positive quantity before it reaches the API", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));
    const before = api.calls.length;

    await user.type(screen.getByTestId("trade-quantity"), "0");
    await user.click(screen.getByTestId("trade-buy"));

    expect(await screen.findByTestId("trade-error")).toHaveTextContent(
      "Enter a quantity greater than zero.",
    );
    expect(api.calls).toHaveLength(before);
  });

  it("rejects a malformed ticker before it reaches the API", async () => {
    const user = userEvent.setup();
    renderTerminal(<TradeBar />);
    await waitFor(() => expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL"));
    const before = api.calls.length;

    await user.clear(screen.getByTestId("trade-ticker"));
    await user.type(screen.getByTestId("trade-quantity"), "5");
    await user.click(screen.getByTestId("trade-buy"));

    expect(await screen.findByTestId("trade-error")).toHaveTextContent(
      "Enter a ticker of 1-10 letters.",
    );
    expect(api.calls).toHaveLength(before);
  });
});

describe("Header", () => {
  it("shows the account, marked to the live stream", async () => {
    installFakeApi({
      portfolio: {
        cash_balance: 8097.5,
        positions: [
          {
            ticker: "AAPL",
            quantity: 10,
            avg_cost: 190.25,
            current_price: 190.25,
            market_value: 1902.5,
            cost_basis: 1902.5,
            unrealized_pnl: 0,
            unrealized_pnl_percent: 0,
            weight: 19.025,
          },
        ],
        positions_value: 1902.5,
        total_value: 10000,
        total_unrealized_pnl: 0,
        total_return_percent: 0,
        starting_cash: 10000,
      },
    });
    renderTerminal(<Header />);
    await waitFor(() =>
      expect(screen.getByTestId("header-cash")).toHaveTextContent("$8,097.50"),
    );

    await stream.emit(makeFrame({ AAPL: 192.1 }));
    expect(screen.getByTestId("header-total-value")).toHaveTextContent("$10,018.50");
    expect(screen.getByTestId("header-return")).toHaveTextContent("+0.18%");
    expect(screen.getByTestId("header-unrealized")).toHaveTextContent("+$18.50");
  });

  it("reports the stream's state in words as well as colour", async () => {
    renderTerminal(<Header />);
    const indicator = screen.getByTestId("connection-status");
    expect(indicator).toHaveAttribute("data-state", "connecting");

    await stream.open();
    expect(indicator).toHaveAttribute("data-state", "live");
    expect(indicator).toHaveTextContent("Live");

    // EventSource retries on its own, so a recoverable error is "reconnecting".
    await stream.fail(false);
    expect(indicator).toHaveAttribute("data-state", "connecting");
    expect(indicator).toHaveTextContent("Reconnecting");

    await stream.fail(true);
    expect(indicator).toHaveAttribute("data-state", "down");
    expect(indicator).toHaveTextContent("Disconnected");
  });
});
