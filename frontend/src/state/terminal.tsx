"use client";

/**
 * Everything the terminal knows that does not arrive over the price stream:
 * the portfolio, the watchlist, the snapshot history, the conversation, and
 * which instrument is selected.
 *
 * Mutations go through here rather than through the panels, so a trade placed
 * on the trade bar and a trade placed by the assistant refresh the same state.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api } from "@/lib/api";
import { markToMarket } from "@/lib/portfolio";
import type {
  ChatMessage,
  Portfolio,
  Side,
  Snapshot,
  Trade,
  WatchlistEntry,
} from "@/lib/types";
import { useMarket } from "./market";

/**
 * Stands in until the first `GET /api/portfolio` lands. `starting_cash` is 0
 * rather than 10000 on purpose: with a total value of 0 the contract's return
 * formula would otherwise render a headline -100.00% before any data exists.
 */
const EMPTY_PORTFOLIO: Portfolio = {
  cash_balance: 0,
  positions: [],
  positions_value: 0,
  total_value: 0,
  total_unrealized_pnl: 0,
  total_return_percent: 0,
  starting_cash: 0,
};

interface TerminalValue {
  /** Repriced against the latest stream frame on every tick. */
  portfolio: Portfolio;
  watchlist: WatchlistEntry[];
  snapshots: Snapshot[];
  messages: ChatMessage[];
  selected: string | null;
  loaded: boolean;
  chatPending: boolean;

  select: (ticker: string) => void;
  trade: (ticker: string, quantity: number, side: Side) => Promise<Trade>;
  addTicker: (ticker: string) => Promise<void>;
  removeTicker: (ticker: string) => Promise<void>;
  send: (message: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const TerminalContext = createContext<TerminalValue | null>(null);

export function TerminalProvider({ children }: { children: ReactNode }) {
  const { frame } = useMarket();

  const [raw, setRaw] = useState<Portfolio>(EMPTY_PORTFOLIO);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // The instrument the user actually picked. Null means "none picked yet", and
  // the effective selection below falls back to the head of the watchlist —
  // derived rather than written back, so there is no cascading render.
  const [chosen, setChosen] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [chatPending, setChatPending] = useState(false);

  const refresh = useCallback(async () => {
    const [portfolio, tickers, history] = await Promise.all([
      api.getPortfolio().catch(() => null),
      api.getWatchlist().catch(() => null),
      api.getHistory().catch(() => null),
    ]);
    if (portfolio) setRaw(portfolio);
    if (tickers) setWatchlist(tickers.tickers);
    if (history) setSnapshots(history.snapshots);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await refresh();
      const history = await api.getChatHistory().catch(() => null);
      if (cancelled) return;
      if (history) setMessages(history.messages);
      setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const selected = chosen ?? watchlist[0]?.ticker ?? null;

  const portfolio = useMemo(() => markToMarket(raw, frame), [raw, frame]);

  const trade = useCallback(
    async (ticker: string, quantity: number, side: Side) => {
      const result = await api.trade(ticker, quantity, side);
      setRaw(result.portfolio);
      // A trade writes a snapshot server-side; pull it so the P&L chart moves.
      void api.getHistory().then((history) => setSnapshots(history.snapshots)).catch(() => {});
      return result.trade;
    },
    [],
  );

  const addTicker = useCallback(async (ticker: string) => {
    const result = await api.addWatchlist(ticker);
    setWatchlist((current) =>
      current.some((entry) => entry.ticker === result.ticker.ticker)
        ? current
        : [...current, result.ticker],
    );
    setChosen(result.ticker.ticker);
  }, []);

  const removeTicker = useCallback(async (ticker: string) => {
    await api.removeWatchlist(ticker);
    setWatchlist((current) => current.filter((entry) => entry.ticker !== ticker));
    setChosen((current) => (current === ticker ? null : current));
  }, []);

  const send = useCallback(
    async (message: string) => {
      const optimistic: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: message,
        actions: null,
        created_at: new Date().toISOString(),
      };
      setMessages((current) => [...current, optimistic]);
      setChatPending(true);
      try {
        const response = await api.chat(message);
        setMessages((current) => [
          ...current,
          {
            id: `assistant-${response.created_at}`,
            role: "assistant",
            content: response.message,
            actions: response.actions,
            created_at: response.created_at,
          },
        ]);
        // The assistant may have traded or edited the watchlist on our behalf.
        if (response.actions.length) await refresh();
      } catch (error) {
        const detail =
          error instanceof ApiError ? error.message : "The assistant is unavailable right now.";
        setMessages((current) => [
          ...current,
          {
            id: `error-${Date.now()}`,
            role: "assistant",
            content: detail,
            actions: [],
            created_at: new Date().toISOString(),
          },
        ]);
      } finally {
        setChatPending(false);
      }
    },
    [refresh],
  );

  const value = useMemo<TerminalValue>(
    () => ({
      portfolio,
      watchlist,
      snapshots,
      messages,
      selected,
      loaded,
      chatPending,
      select: setChosen,
      trade,
      addTicker,
      removeTicker,
      send,
      refresh,
    }),
    [
      portfolio,
      watchlist,
      snapshots,
      messages,
      selected,
      loaded,
      chatPending,
      trade,
      addTicker,
      removeTicker,
      send,
      refresh,
    ],
  );

  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}

export function useTerminal(): TerminalValue {
  const value = useContext(TerminalContext);
  if (!value) throw new Error("useTerminal must be used inside a TerminalProvider");
  return value;
}
