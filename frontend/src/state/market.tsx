"use client";

/**
 * The SSE spine. One `EventSource` serves the entire page: every panel reads
 * prices from this provider rather than opening a stream of its own.
 *
 * `EventSource` reconnects by itself, honouring the server's `retry:` directive,
 * so there is deliberately no reconnection logic here — only status reporting.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { PRICE_STREAM_URL } from "@/lib/api";
import type { PriceFrame, PriceTick } from "@/lib/types";

/** How many ticks of per-ticker history the sparklines and price chart keep. */
export const HISTORY_LIMIT = 900;

export type ConnectionStatus = "connecting" | "live" | "down";

export interface PricePoint {
  /** Unix epoch float seconds, straight off the wire. */
  t: number;
  p: number;
}

interface MarketValue {
  /** Latest tick per ticker, replaced wholesale on each frame. */
  frame: PriceFrame;
  status: ConnectionStatus;
  /** Wall-clock ms of the most recent frame, or null before the first one. */
  lastFrameAt: number | null;
  /**
   * Per-ticker count of ticks that actually moved the price. Used as a React
   * key so the flash animation restarts even on back-to-back ticks.
   */
  pulses: Record<string, number>;
  /** Price history accumulated since page load, oldest first. */
  seriesFor: (ticker: string) => PricePoint[];
  tickerOf: (ticker: string) => PriceTick | undefined;
}

const MarketContext = createContext<MarketValue | null>(null);

export function MarketProvider({ children }: { children: ReactNode }) {
  const [frame, setFrame] = useState<PriceFrame>({});
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null);
  const [pulses, setPulses] = useState<Record<string, number>>({});

  // History is large and append-only; keeping it in a ref avoids copying every
  // series on every frame. Consumers re-render off `frame`, which changes with it.
  const historyRef = useRef<Map<string, PricePoint[]>>(new Map());

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;

    const source = new EventSource(PRICE_STREAM_URL);

    source.onopen = () => setStatus("live");

    source.onmessage = (event: MessageEvent) => {
      let next: PriceFrame;
      try {
        next = JSON.parse(event.data as string) as PriceFrame;
      } catch {
        return; // A malformed frame is dropped; the next one is 500ms away.
      }
      if (!next || typeof next !== "object") return;

      const history = historyRef.current;
      const movedTickers: string[] = [];

      for (const [ticker, tick] of Object.entries(next)) {
        if (!tick || typeof tick.price !== "number") continue;
        const series = history.get(ticker) ?? [];
        series.push({ t: tick.timestamp, p: tick.price });
        if (series.length > HISTORY_LIMIT) series.splice(0, series.length - HISTORY_LIMIT);
        history.set(ticker, series);
        if (tick.direction !== "flat") movedTickers.push(ticker);
      }

      setFrame(next);
      setStatus("live");
      setLastFrameAt(Date.now());
      if (movedTickers.length) {
        setPulses((current) => {
          const updated = { ...current };
          for (const ticker of movedTickers) updated[ticker] = (updated[ticker] ?? 0) + 1;
          return updated;
        });
      }
    };

    source.onerror = () => {
      // readyState tells reconnecting (CONNECTING) apart from gone (CLOSED).
      setStatus(source.readyState === EventSource.CLOSED ? "down" : "connecting");
    };

    return () => source.close();
  }, []);

  const seriesFor = useCallback((ticker: string) => historyRef.current.get(ticker) ?? [], []);
  const tickerOf = useCallback((ticker: string) => frame[ticker], [frame]);

  const value = useMemo<MarketValue>(
    () => ({ frame, status, lastFrameAt, pulses, seriesFor, tickerOf }),
    [frame, status, lastFrameAt, pulses, seriesFor, tickerOf],
  );

  return <MarketContext.Provider value={value}>{children}</MarketContext.Provider>;
}

export function useMarket(): MarketValue {
  const value = useContext(MarketContext);
  if (!value) throw new Error("useMarket must be used inside a MarketProvider");
  return value;
}
