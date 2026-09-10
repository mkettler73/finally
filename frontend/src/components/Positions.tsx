"use client";

import {
  formatPrice,
  formatQuantity,
  formatUsd,
} from "@/lib/format";
import { useMarket } from "@/state/market";
import { useTerminal } from "@/state/terminal";
import { Delta } from "./Delta";
import { Panel } from "./Panel";

const HEAD = "px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-mute";

export function Positions() {
  const { portfolio, selected, select } = useTerminal();
  const { pulses, frame } = useMarket();

  return (
    <Panel
      label="Positions"
      testId="positions"
      meta={
        <>
          <span className="num text-[10px] text-ink-mute">
            {portfolio.positions.length} held
          </span>
          <Delta
            value={portfolio.total_unrealized_pnl}
            kind="usd"
            className="text-[11px]"
            testId="positions-total-pnl"
          />
        </>
      }
      bodyClassName="overflow-auto"
    >
      <table className="w-full border-collapse text-[12px]" data-testid="positions-table">
        <thead className="sticky top-0 z-10 bg-rail">
          <tr className="border-b border-line">
            <th scope="col" className={`${HEAD} text-left`}>
              Ticker
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Qty
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Avg cost
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Last
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Value
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Unrealised
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Return
            </th>
          </tr>
        </thead>
        <tbody>
          {portfolio.positions.length === 0 ? (
            <tr>
              <td colSpan={7} className="px-3 py-6 text-center text-ink-mute" data-testid="positions-empty">
                No open positions. Use the trade bar below to buy.
              </td>
            </tr>
          ) : (
            portfolio.positions.map((position) => (
              <tr
                key={position.ticker}
                onClick={() => select(position.ticker)}
                className={`cursor-pointer border-b border-line/60 transition-colors ${
                  selected === position.ticker ? "bg-raised" : "hover:bg-raised/60"
                }`}
                data-testid="positions-row"
                data-ticker={position.ticker}
              >
                <th
                  scope="row"
                  className={`px-3 py-1.5 text-left font-mono text-[12px] font-medium ${
                    selected === position.ticker
                      ? "border-l-2 border-l-accent text-ink"
                      : "border-l-2 border-l-transparent text-ink"
                  }`}
                >
                  {position.ticker}
                </th>
                <td className="num px-3 py-1.5 text-right font-mono text-ink-dim">
                  {formatQuantity(position.quantity)}
                </td>
                <td className="num px-3 py-1.5 text-right font-mono text-ink-dim">
                  {formatPrice(position.avg_cost)}
                </td>
                <td className="px-3 py-1.5 text-right">
                  <span
                    key={pulses[position.ticker] ?? 0}
                    data-flash={
                      frame[position.ticker]?.direction === "flat"
                        ? undefined
                        : frame[position.ticker]?.direction
                    }
                    data-testid="positions-price"
                    data-ticker={position.ticker}
                    className="num rounded-[2px] px-1 font-mono text-ink"
                  >
                    {formatPrice(position.current_price)}
                  </span>
                </td>
                <td className="num px-3 py-1.5 text-right font-mono text-ink-dim">
                  {formatUsd(position.market_value)}
                </td>
                <td className="px-3 py-1.5 text-right">
                  <Delta
                    value={position.unrealized_pnl}
                    kind="usd"
                    showGlyph={false}
                    className="font-mono"
                    testId="positions-pnl"
                  />
                </td>
                <td className="px-3 py-1.5 text-right">
                  <Delta
                    value={position.unrealized_pnl_percent}
                    className="font-mono"
                    testId="positions-pnl-percent"
                  />
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </Panel>
  );
}
