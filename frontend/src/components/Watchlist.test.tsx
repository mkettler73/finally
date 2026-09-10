import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { makeFrame, sampleWatchlist } from "@/lib/fixtures";
import { installFakeApi, installFakeStream, renderTerminal, type FakeApi, type FakeStream } from "@/test/harness";
import { Watchlist } from "./Watchlist";

let stream: FakeStream;
let api: FakeApi;

const watchlist = sampleWatchlist.slice(0, 3); // AAPL, GOOGL, MSFT

beforeEach(() => {
  stream = installFakeStream();
  api = installFakeApi({ watchlist });
});

async function mount() {
  renderTerminal(<Watchlist />);
  await screen.findByText("AAPL");
}

const priceOf = (ticker: string) =>
  screen
    .getAllByTestId("watchlist-price")
    .find((node) => node.dataset.ticker === ticker)!;

describe("Watchlist", () => {
  it("lists the watched instruments in the order the API returned them", async () => {
    await mount();
    const rows = screen.getAllByTestId("watchlist-row");
    expect(rows.map((row) => row.dataset.ticker)).toEqual(["AAPL", "GOOGL", "MSFT"]);
  });

  it("prefers the streamed price over the one fetched with the watchlist", async () => {
    await mount();
    expect(priceOf("AAPL")).toHaveTextContent("190.20");

    await stream.emit(makeFrame({ AAPL: 191.42 }, { AAPL: 190.2 }));
    expect(priceOf("AAPL")).toHaveTextContent("191.42");
  });

  it("flashes up on an uptick and down on a downtick", async () => {
    await mount();
    // No tick has been seen yet, so nothing is flashing.
    expect(priceOf("AAPL")).not.toHaveAttribute("data-flash");

    await stream.emit(makeFrame({ AAPL: 191 }, { AAPL: 190.2 }));
    expect(priceOf("AAPL")).toHaveAttribute("data-flash", "up");

    await stream.emit(makeFrame({ AAPL: 189 }, { AAPL: 191 }));
    expect(priceOf("AAPL")).toHaveAttribute("data-flash", "down");
  });

  it("does not flash when the price is unchanged", async () => {
    await mount();
    await stream.emit(makeFrame({ AAPL: 190.2 }, { AAPL: 190.2 }));
    expect(priceOf("AAPL")).not.toHaveAttribute("data-flash");
  });

  it("recomputes the day change from the session open as prices stream in", async () => {
    await mount();
    const row = screen
      .getAllByTestId("watchlist-row")
      .find((candidate) => candidate.dataset.ticker === "AAPL")!;
    const open = watchlist[0].session_open!;

    await stream.emit(makeFrame({ AAPL: open * 1.02 }));
    const change = within(row).getByTestId("watchlist-change");
    expect(change).toHaveTextContent("+2.00%");
    expect(change).toHaveAttribute("data-direction", "up");
  });

  it("accumulates sparkline points from the stream", async () => {
    await mount();
    const sparkOf = () =>
      screen.getAllByTestId("watchlist-sparkline")[0] as unknown as HTMLElement;
    expect(sparkOf().dataset.points).toBe("0");

    await stream.emit(makeFrame({ AAPL: 190.3 }));
    await stream.emit(makeFrame({ AAPL: 190.4 }));
    expect(sparkOf().dataset.points).toBe("2");
  });

  it("adds a ticker and selects it", async () => {
    const user = userEvent.setup();
    await mount();

    await user.type(screen.getByTestId("watchlist-add-input"), "pypl");
    await user.click(screen.getByTestId("watchlist-add-submit"));

    await waitFor(() => expect(screen.getByText("PYPL")).toBeInTheDocument());
    expect(api.calls).toContainEqual({
      method: "POST",
      path: "/api/watchlist",
      body: { ticker: "PYPL" },
    });
    const added = screen
      .getAllByTestId("watchlist-row")
      .find((row) => row.dataset.ticker === "PYPL")!;
    expect(added.dataset.selected).toBe("true");
  });

  it("rejects a malformed ticker without calling the API", async () => {
    const user = userEvent.setup();
    await mount();
    const before = api.calls.length;

    await user.type(screen.getByTestId("watchlist-add-input"), "AA PL");
    await user.click(screen.getByTestId("watchlist-add-submit"));

    expect(await screen.findByTestId("watchlist-add-error")).toHaveTextContent(
      "Enter a ticker of 1-10 letters.",
    );
    expect(api.calls).toHaveLength(before);
  });

  it("shows the API's own message when the ticker is already watched", async () => {
    const user = userEvent.setup();
    await mount();
    api.failNext("/api/watchlist", "TICKER_ALREADY_WATCHED", "AAPL is already watched.", 409);

    await user.type(screen.getByTestId("watchlist-add-input"), "AAPL");
    await user.click(screen.getByTestId("watchlist-add-submit"));

    expect(await screen.findByTestId("watchlist-add-error")).toHaveTextContent(
      "AAPL is already watched.",
    );
  });

  it("removes a ticker", async () => {
    const user = userEvent.setup();
    await mount();

    const remove = screen
      .getAllByTestId("watchlist-remove")
      .find((button) => button.dataset.ticker === "GOOGL")!;
    await user.click(remove);

    await waitFor(() =>
      expect(screen.queryByText("GOOGL")).not.toBeInTheDocument(),
    );
    expect(api.calls).toContainEqual({
      method: "DELETE",
      path: "/api/watchlist/GOOGL",
      body: undefined,
    });
  });

  it("selects the clicked instrument", async () => {
    const user = userEvent.setup();
    await mount();

    // The default selection lands on the render after the list arrives.
    await waitFor(() =>
      expect(screen.getAllByTestId("watchlist-row")[0].dataset.selected).toBe("true"),
    );

    await user.click(screen.getAllByTestId("watchlist-row")[2]);
    expect(screen.getAllByTestId("watchlist-row")[2].dataset.selected).toBe("true");
    expect(screen.getAllByTestId("watchlist-row")[0].dataset.selected).toBe("false");
  });

  it("invites a first ticker when the watchlist is empty", async () => {
    installFakeApi({ watchlist: [] });
    renderTerminal(<Watchlist />);
    expect(await screen.findByTestId("watchlist-empty")).toHaveTextContent("Add a ticker");
  });
});
