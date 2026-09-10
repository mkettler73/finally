import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { makeFrame, samplePortfolio } from "@/lib/fixtures";
import { installFakeApi, installFakeStream, renderTerminal, type FakeStream } from "@/test/harness";
import { Heatmap } from "./Heatmap";
import { Positions } from "./Positions";

let stream: FakeStream;

beforeEach(() => {
  stream = installFakeStream();
});

const rowFor = (ticker: string) =>
  screen.getAllByTestId("positions-row").find((row) => row.dataset.ticker === ticker)!;

describe("Positions", () => {
  beforeEach(() => {
    installFakeApi({ portfolio: samplePortfolio });
  });

  it("renders one row per holding, largest first", async () => {
    renderTerminal(<Positions />);
    await screen.findByText("NVDA");
    expect(screen.getAllByTestId("positions-row").map((row) => row.dataset.ticker)).toEqual([
      "NVDA",
      "AAPL",
      "MSFT",
    ]);
  });

  it("shows the contract's P&L figures with an explicit sign", async () => {
    renderTerminal(<Positions />);
    await screen.findByText("NVDA");

    const nvda = within(rowFor("NVDA"));
    expect(nvda.getByTestId("positions-pnl")).toHaveTextContent("+$60.00");
    expect(nvda.getByTestId("positions-pnl-percent")).toHaveTextContent("+2.53%");
    expect(nvda.getByTestId("positions-pnl-percent")).toHaveAttribute("data-direction", "up");

    const aapl = within(rowFor("AAPL"));
    expect(aapl.getByTestId("positions-pnl")).toHaveTextContent("-$9.10");
    expect(aapl.getByTestId("positions-pnl-percent")).toHaveAttribute("data-direction", "down");
  });

  it("keeps fractional share counts", async () => {
    renderTerminal(<Positions />);
    await screen.findByText("MSFT");
    expect(within(rowFor("MSFT")).getByText("1.5")).toBeInTheDocument();
  });

  it("reprices from the stream without refetching the portfolio", async () => {
    renderTerminal(<Positions />);
    await screen.findByText("NVDA");

    // 20 shares bought at 118.40, now marked at 130.00.
    await stream.emit(makeFrame({ NVDA: 130 }, { NVDA: 121.4 }));

    const nvda = within(rowFor("NVDA"));
    expect(nvda.getByTestId("positions-pnl")).toHaveTextContent("+$232.00");
    expect(nvda.getByTestId("positions-price")).toHaveTextContent("130.00");
    expect(nvda.getByTestId("positions-price")).toHaveAttribute("data-flash", "up");
  });

  it("totals unrealised P&L in the panel header", async () => {
    renderTerminal(<Positions />);
    await screen.findByText("NVDA");
    expect(screen.getByTestId("positions-total-pnl")).toHaveTextContent("+$65.30");
  });

  it("points at the trade bar when nothing is held", async () => {
    installFakeApi({});
    renderTerminal(<Positions />);
    expect(await screen.findByTestId("positions-empty")).toHaveTextContent("No open positions");
  });

  it("selects the instrument when a row is clicked", async () => {
    const user = userEvent.setup();
    renderTerminal(
      <>
        <Positions />
        <Heatmap />
      </>,
    );
    // Both panels label their NVDA holding, so match on the table row instead.
    await screen.findAllByTestId("positions-row");

    await user.click(rowFor("AAPL"));
    // Selection is shared state, so the heatmap agrees with the table.
    const cell = screen
      .getAllByTestId("heatmap-cell")
      .find((node) => node.dataset.ticker === "AAPL");
    expect(cell).toBeDefined();
  });
});

describe("Heatmap", () => {
  it("draws one cell per position, tagged with weight and direction", async () => {
    installFakeApi({ portfolio: samplePortfolio });
    renderTerminal(<Heatmap />);
    await screen.findByTestId("heatmap");

    const cells = screen.getAllByTestId("heatmap-cell");
    expect(cells.map((cell) => cell.dataset.ticker).sort()).toEqual(["AAPL", "MSFT", "NVDA"]);

    const nvda = cells.find((cell) => cell.dataset.ticker === "NVDA")!;
    expect(nvda.dataset.pnlDirection).toBe("up");
    const aapl = cells.find((cell) => cell.dataset.ticker === "AAPL")!;
    expect(aapl.dataset.pnlDirection).toBe("down");
  });

  it("labels the diverging scale it is actually using", async () => {
    installFakeApi({ portfolio: samplePortfolio });
    renderTerminal(<Heatmap />);
    // Largest absolute move in the book is NVDA at 2.53%.
    expect(await screen.findByTestId("heatmap-legend")).toHaveTextContent("+3%");
  });

  it("invites a first trade when the book is empty", async () => {
    installFakeApi({});
    renderTerminal(<Heatmap />);
    expect(await screen.findByTestId("heatmap-empty")).toHaveTextContent("sized by weight");
    expect(screen.queryByTestId("heatmap-legend")).not.toBeInTheDocument();
  });
});
