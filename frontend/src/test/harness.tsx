/**
 * Test harness. The panels only make sense inside the two providers, and the
 * providers only make sense with a stream and an API behind them, so both are
 * faked here rather than in each test file.
 */

import { render, type RenderResult } from "@testing-library/react";
import { act } from "react";
import { vi } from "vitest";
import type { ReactNode } from "react";

import type { ChatMessage, Portfolio, PriceFrame, Snapshot, WatchlistEntry } from "@/lib/types";
import { MarketProvider } from "@/state/market";
import { TerminalProvider } from "@/state/terminal";

export interface FakeStream {
  /** Push one SSE frame to every open connection. */
  emit: (frame: PriceFrame) => Promise<void>;
  /** Simulate a transport failure. `closed` distinguishes gone from retrying. */
  fail: (closed?: boolean) => Promise<void>;
  open: () => Promise<void>;
}

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.CONNECTING;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener() {}
  removeEventListener() {}
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

/** Replaces `globalThis.EventSource` for the duration of a test file. */
export function installFakeStream(): FakeStream {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;

  const each = async (run: (source: FakeEventSource) => void) => {
    await act(async () => {
      FakeEventSource.instances.forEach(run);
    });
  };

  return {
    open: () =>
      each((source) => {
        source.readyState = FakeEventSource.OPEN;
        source.onopen?.(new Event("open"));
      }),
    emit: (frame) =>
      each((source) => {
        source.readyState = FakeEventSource.OPEN;
        source.onmessage?.(new MessageEvent("message", { data: JSON.stringify(frame) }));
      }),
    fail: (closed = false) =>
      each((source) => {
        source.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
        source.onerror?.(new Event("error"));
      }),
  };
}

export interface ApiState {
  portfolio: Portfolio;
  watchlist: WatchlistEntry[];
  snapshots: Snapshot[];
  messages: ChatMessage[];
}

export interface FakeApi {
  state: ApiState;
  /** Queue a one-shot failure for the next call to a path. */
  failNext: (path: string, code: string, message: string, status?: number) => void;
  calls: { method: string; path: string; body: unknown }[];
  fetch: ReturnType<typeof vi.fn>;
}

/** Installs a `fetch` that answers the endpoints in API_CONTRACT.md. */
export function installFakeApi(initial: Partial<ApiState> = {}): FakeApi {
  const state: ApiState = {
    portfolio: initial.portfolio ?? {
      cash_balance: 10000,
      positions: [],
      positions_value: 0,
      total_value: 10000,
      total_unrealized_pnl: 0,
      total_return_percent: 0,
      starting_cash: 10000,
    },
    watchlist: initial.watchlist ?? [],
    snapshots: initial.snapshots ?? [],
    messages: initial.messages ?? [],
  };

  const failures = new Map<string, { code: string; message: string; status: number }>();
  const calls: FakeApi["calls"] = [];

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.split("?")[0];
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });

    const queued = failures.get(path);
    if (queued) {
      failures.delete(path);
      return json({ detail: { code: queued.code, message: queued.message } }, queued.status);
    }

    if (path === "/api/portfolio") return json(state.portfolio);
    if (path === "/api/portfolio/history") return json({ snapshots: state.snapshots });
    if (path === "/api/watchlist" && method === "GET") return json({ tickers: state.watchlist });
    if (path === "/api/watchlist" && method === "POST") {
      const entry: WatchlistEntry = {
        ticker: String(body.ticker).toUpperCase(),
        price: null,
        previous_price: null,
        change: null,
        change_percent: null,
        direction: "flat",
        session_open: null,
        session_change: null,
        session_change_percent: null,
        added_at: new Date().toISOString(),
      };
      state.watchlist = [...state.watchlist, entry];
      return json({ ticker: entry }, 201);
    }
    if (path.startsWith("/api/watchlist/") && method === "DELETE") {
      const ticker = decodeURIComponent(path.slice("/api/watchlist/".length));
      state.watchlist = state.watchlist.filter((entry) => entry.ticker !== ticker);
      return json({ ticker, removed: true });
    }
    if (path === "/api/chat/history") return json({ messages: state.messages });
    if (path === "/api/chat" && method === "POST") {
      return json({ message: "Acknowledged.", actions: [], created_at: "2026-09-09T14:32:05.000000Z" });
    }
    if (path === "/api/portfolio/trade" && method === "POST") {
      return json({
        trade: {
          id: "t1",
          ticker: String(body.ticker).toUpperCase(),
          side: body.side,
          quantity: body.quantity,
          price: 190.25,
          total: body.quantity * 190.25,
          executed_at: "2026-09-09T14:32:05.000000Z",
        },
        portfolio: state.portfolio,
      });
    }
    return json({ detail: { code: "INTERNAL_ERROR", message: `No route for ${method} ${path}` } }, 500);
  });

  globalThis.fetch = fetchMock as unknown as typeof fetch;

  return {
    state,
    calls,
    fetch: fetchMock,
    failNext: (path, code, message, status = 400) =>
      failures.set(path, { code, message, status }),
  };
}

export function renderTerminal(ui: ReactNode): RenderResult {
  return render(
    <MarketProvider>
      <TerminalProvider>{ui}</TerminalProvider>
    </MarketProvider>,
  );
}
