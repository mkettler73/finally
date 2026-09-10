"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { formatPrice, formatUsd, isValidTicker, normalizeTicker } from "@/lib/format";
import { estimateOrder } from "@/lib/portfolio";
import type { Side } from "@/lib/types";
import { useMarket } from "@/state/market";
import { useTerminal } from "@/state/terminal";

type Feedback = { tone: "ok" | "error"; text: string } | null;

const FIELD =
  "num rounded-sm border border-line-strong bg-panel px-2 py-1.5 font-mono text-[13px] text-ink placeholder:font-sans placeholder:text-ink-mute focus:border-accent focus:outline-none";

/**
 * Market orders, instant fill, no confirmation step. The panel's job is to make
 * the cost of the order obvious before it is placed, so the estimate updates
 * with the stream while the quantity is being typed.
 */
export function TradeBar() {
  const { frame } = useMarket();
  const { selected, trade } = useTerminal();

  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [pending, setPending] = useState<Side | null>(null);
  const editedRef = useRef(false);

  // Follow the chart selection until the field is edited by hand.
  useEffect(() => {
    if (!editedRef.current && selected) setTicker(selected);
  }, [selected]);

  const normalized = normalizeTicker(ticker);
  const price = frame[normalized]?.price ?? null;
  const parsedQuantity = Number(quantity);
  const estimate = estimateOrder(price, parsedQuantity);

  const submit = async (side: Side) => {
    setFeedback(null);
    if (!isValidTicker(normalized)) {
      setFeedback({ tone: "error", text: "Enter a ticker of 1-10 letters." });
      return;
    }
    if (!Number.isFinite(parsedQuantity) || parsedQuantity <= 0) {
      setFeedback({ tone: "error", text: "Enter a quantity greater than zero." });
      return;
    }

    setPending(side);
    try {
      const executed = await trade(normalized, parsedQuantity, side);
      setFeedback({
        tone: "ok",
        text: `${side === "buy" ? "Bought" : "Sold"} ${executed.quantity} ${executed.ticker} at ${formatPrice(executed.price)} — ${formatUsd(executed.total)}`,
      });
      setQuantity("");
    } catch (error) {
      // The API's message is written for a person; show it verbatim.
      setFeedback({
        tone: "error",
        text: error instanceof ApiError ? error.message : "The trade could not be placed.",
      });
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="flex h-11 items-center gap-3 border-t border-line bg-rail px-3" data-testid="trade-bar">
      <label className="panel-label" htmlFor="trade-ticker">
        Ticker
      </label>
      <input
        id="trade-ticker"
        value={ticker}
        onChange={(event) => {
          editedRef.current = true;
          setTicker(event.target.value.toUpperCase());
        }}
        maxLength={10}
        placeholder="AAPL"
        className={`${FIELD} w-24`}
        data-testid="trade-ticker"
      />

      <label className="panel-label" htmlFor="trade-quantity">
        Quantity
      </label>
      <input
        id="trade-quantity"
        value={quantity}
        onChange={(event) => setQuantity(event.target.value)}
        inputMode="decimal"
        placeholder="0"
        className={`${FIELD} w-24`}
        data-testid="trade-quantity"
      />

      <span className="num font-mono text-[12px] text-ink-dim" data-testid="trade-estimate">
        {price == null
          ? "No price yet"
          : estimate == null
            ? `${formatPrice(price)} a share`
            : `${formatPrice(price)} a share — ${formatUsd(estimate)}`}
      </span>

      {feedback ? (
        <p
          role={feedback.tone === "error" ? "alert" : "status"}
          className={`max-w-[42ch] truncate text-[11px] ${feedback.tone === "error" ? "text-down" : "text-up"}`}
          data-testid={feedback.tone === "error" ? "trade-error" : "trade-status"}
          title={feedback.text}
        >
          {feedback.text}
        </p>
      ) : null}

      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          onClick={() => void submit("buy")}
          disabled={pending !== null}
          className="min-w-[76px] rounded-sm bg-up px-4 py-1.5 text-[12px] font-semibold tracking-wide text-[#04140e] transition-opacity hover:opacity-90 disabled:opacity-50"
          data-testid="trade-buy"
        >
          {pending === "buy" ? "Buying…" : "Buy"}
        </button>
        <button
          type="button"
          onClick={() => void submit("sell")}
          disabled={pending !== null}
          className="min-w-[76px] rounded-sm bg-down px-4 py-1.5 text-[12px] font-semibold tracking-wide text-[#1a0703] transition-opacity hover:opacity-90 disabled:opacity-50"
          data-testid="trade-sell"
        >
          {pending === "sell" ? "Selling…" : "Sell"}
        </button>
      </div>

    </div>
  );
}
