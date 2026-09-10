# FinAlly — HTTP API Contract (FROZEN)

This document is the binding contract between the **Backend API Engineer**, the
**LLM Engineer**, the **Frontend Engineer** and the **Integration Tester**. Every
shape below is normative. Build against it without waiting for the other side to
exist.

**Changing this document requires agreement from the affected agents.** If you
believe a shape here is wrong, message the Team Lead (the orchestrating session)
rather than unilaterally diverging — a silent divergence is how the frontend ends
up parsing a field the backend never sends.

---

## 0. Conventions

| Rule | Value |
|---|---|
| Base path | All endpoints are under `/api`. Same origin as the frontend — no CORS. |
| Content type | `application/json` for everything except `/api/stream/prices` (`text/event-stream`). |
| Timestamps | ISO-8601 UTC strings with a `Z` suffix, e.g. `"2026-09-09T14:32:05.123456Z"`. Except SSE frames, which carry Unix-epoch **float seconds** (existing market-data behaviour, do not change). |
| Money | Raw unrounded `float`. The **frontend** formats to 2dp. Backends must not pre-round, or P&L sums drift. |
| Percentages | Any field ending `_percent` is a **percentage**, not a ratio: `1.5` means 1.5%. |
| Tickers | Always normalised uppercase in responses. Requests accept any case/whitespace; the backend normalises via `MarketDataSource.normalize_ticker()`. |
| Quantities | `float` — fractional shares are supported throughout. |
| `user_id` | Always `"default"`. Never appears in any request or response body. |

### Error envelope

Every 4xx/5xx response body is:

```json
{ "detail": { "code": "INSUFFICIENT_CASH", "message": "Need $1,902.50 but only $500.00 available." } }
```

`code` is a stable machine-readable enum; `message` is human-readable and is what
the frontend renders and what the LLM layer feeds back to the model.

**Error codes:**

| Code | Status | Meaning |
|---|---|---|
| `INVALID_QUANTITY` | 400 | Quantity is not a finite number > 0. |
| `INVALID_SIDE` | 400 | `side` is not `"buy"` or `"sell"`. |
| `INVALID_TICKER` | 400 | Ticker is empty, or not 1–10 chars of `A-Z`/`.`/`-`. |
| `PRICE_UNAVAILABLE` | 400 | Ticker has no price in the cache — cannot value the trade. |
| `INSUFFICIENT_CASH` | 400 | Buy costs more than `cash_balance`. |
| `INSUFFICIENT_SHARES` | 400 | Sell exceeds the held quantity. |
| `TICKER_ALREADY_WATCHED` | 409 | Ticker already on the watchlist. |
| `TICKER_NOT_WATCHED` | 404 | Ticker not on the watchlist. |
| `WATCHLIST_FULL` | 400 | Watchlist is at the 30-ticker cap. |
| `LLM_ERROR` | 502 | Upstream LLM call failed or returned unparseable output. |
| `INTERNAL_ERROR` | 500 | Anything else. |

Validation errors raised by FastAPI/Pydantic itself must be mapped into this same
envelope by an exception handler — the frontend must never see FastAPI's default
`{"detail": [{"loc": ...}]}` array shape.

---

## 1. System

### `GET /api/health`

**200**
```json
{ "status": "ok" }
```
Already implemented in `backend/app/main.py`. Do not change its shape — the
Dockerfile healthcheck and E2E readiness probe both depend on it.

---

## 2. Market data (already built — do not modify)

### `GET /api/stream/prices` — SSE

Implemented in `backend/app/market/stream.py`. Frames are emitted only when the
price cache version moves (~500ms cadence). Each frame:

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.5,"previous_price":190.42,"timestamp":1789243925.11,"change":0.08,"change_percent":0.042,"direction":"up"},"GOOGL":{...}}
```

- The payload is an **object keyed by ticker**, not an array.
- `timestamp` is Unix epoch **float seconds** (not ISO) — this endpoint is the one
  exception to the timestamp rule.
- `direction` is `"up" | "down" | "flat"`, computed tick-over-tick.
- `change` / `change_percent` are **tick-over-tick**, not daily. For a day-change
  figure use `session_change_percent` from `/api/watchlist` (§4).
- Lines beginning `:` are heartbeat comments (every 15s idle) — `EventSource`
  ignores them automatically.
- The stream carries **every ticker the backend tracks**, which is the union of
  the watchlist and any ticker held in a position.

Frontend: consume with the native `EventSource`. Do not add reconnection logic —
`EventSource` retries on its own, honouring the `retry: 1000` directive.

---

## 3. Portfolio

### `GET /api/portfolio`

**200**
```json
{
  "cash_balance": 8097.5,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10.0,
      "avg_cost": 190.25,
      "current_price": 192.1,
      "market_value": 1921.0,
      "cost_basis": 1902.5,
      "unrealized_pnl": 18.5,
      "unrealized_pnl_percent": 0.9724,
      "weight": 19.1747
    }
  ],
  "positions_value": 1921.0,
  "total_value": 10018.5,
  "total_unrealized_pnl": 18.5,
  "total_return_percent": 0.185,
  "starting_cash": 10000.0
}
```

Field definitions — these are the single source of truth for the P&L math:

| Field | Definition |
|---|---|
| `cost_basis` | `quantity * avg_cost` |
| `market_value` | `quantity * current_price` |
| `unrealized_pnl` | `market_value - cost_basis` |
| `unrealized_pnl_percent` | `unrealized_pnl / cost_basis * 100`; `0.0` when `cost_basis == 0` |
| `weight` | `market_value / total_value * 100`; `0.0` when `total_value == 0` |
| `positions_value` | Sum of every `market_value` |
| `total_value` | `cash_balance + positions_value` |
| `total_unrealized_pnl` | Sum of every `unrealized_pnl` |
| `total_return_percent` | `(total_value - starting_cash) / starting_cash * 100` |
| `starting_cash` | Constant `10000.0` |

- `positions` is sorted by `market_value` descending.
- A position whose ticker has **no cached price** reports `current_price` equal to
  its `avg_cost` (so P&L reads 0 rather than nonsense) — never `null`.
- Positions with `quantity == 0` are deleted, never returned.

### `POST /api/portfolio/trade`

**Request**
```json
{ "ticker": "AAPL", "quantity": 10, "side": "buy" }
```
`side` is `"buy" | "sell"`. `quantity` is a positive float.

**200**
```json
{
  "trade": {
    "id": "9f1c...",
    "ticker": "AAPL",
    "side": "buy",
    "quantity": 10.0,
    "price": 190.25,
    "total": 1902.5,
    "executed_at": "2026-09-09T14:32:05.123456Z"
  },
  "portfolio": { "...": "the full GET /api/portfolio object, post-trade" }
}
```

Execution rules:
- Market order, instant fill at `price_cache.get_price(ticker)`. No fees, no slippage, no partial fills.
- **Buy:** requires `cash_balance >= quantity * price`, else `INSUFFICIENT_CASH`.
  New `avg_cost = (old_qty*old_avg + qty*price) / (old_qty + qty)`.
- **Sell:** requires `held_quantity >= quantity`, else `INSUFFICIENT_SHARES`.
  `avg_cost` is **unchanged** by a sell. If the resulting quantity is `<= 1e-9`,
  the position row is deleted.
- Buying a ticker not on the watchlist is allowed and does **not** add it to the
  watchlist, but the ticker **is** added to the market data source so it gets priced.
- Every trade appends a row to `trades` and writes a `portfolio_snapshots` row
  immediately, in the same transaction.

Returning the whole portfolio alongside the trade is deliberate: it saves the
frontend a follow-up round trip and removes a class of stale-UI bug.

### `GET /api/portfolio/history?limit=500`

**200**
```json
{
  "snapshots": [
    { "total_value": 10000.0, "recorded_at": "2026-09-09T14:30:00.000000Z" },
    { "total_value": 10018.5, "recorded_at": "2026-09-09T14:32:05.123456Z" }
  ]
}
```
- Sorted **oldest first** — the chart plots left to right without re-sorting.
- `limit` defaults to 500, max 5000; returns the most recent `limit` snapshots,
  still in oldest-first order.
- Always contains at least one point (the seed snapshot written at first init),
  so the P&L chart is never empty.

---

## 4. Watchlist

### `GET /api/watchlist`

**200**
```json
{
  "tickers": [
    {
      "ticker": "AAPL",
      "price": 192.1,
      "previous_price": 191.9,
      "change": 0.2,
      "change_percent": 0.1042,
      "direction": "up",
      "session_open": 190.0,
      "session_change": 2.1,
      "session_change_percent": 1.1053,
      "added_at": "2026-09-09T14:30:00.000000Z"
    }
  ]
}
```

- `price`, `previous_price`, `change`, `change_percent`, `direction` mirror the
  price cache (tick-over-tick), and are `null` / `"flat"` when the ticker has no
  price yet.
- `session_open` is the **first price observed for that ticker since backend
  process start**, tracked by the backend API layer (`SessionOpenTracker`), not by
  the market module. `session_change_percent` is the "day change %" the watchlist
  UI displays. `null` until a price exists.
- Sorted by `added_at` ascending, so the default ten keep their seeded order.

### `POST /api/watchlist`

**Request** `{ "ticker": "PYPL" }`

**201**
```json
{ "ticker": { "...": "one entry, same shape as a GET /api/watchlist row" } }
```
Adds the ticker to the DB **and** to the live market data source
(`await source.add_ticker(...)`) so it starts streaming immediately. Duplicate →
`409 TICKER_ALREADY_WATCHED`. Cap of 30 → `400 WATCHLIST_FULL`.

### `DELETE /api/watchlist/{ticker}`

**200** `{ "ticker": "PYPL", "removed": true }`

Removes from the DB. **Only** calls `source.remove_ticker()` if no open position
holds that ticker — a held position must keep streaming so the portfolio can be
valued. Unknown ticker → `404 TICKER_NOT_WATCHED`.

---

## 5. Chat

### `POST /api/chat`

**Request** `{ "message": "Buy 5 shares of NVDA" }`

**200**
```json
{
  "message": "Bought 5 NVDA at $121.40 — that's $607.00, leaving you $9,393.00 in cash.",
  "actions": [
    { "type": "trade",  "status": "ok",    "detail": "Bought 5 NVDA @ $121.40", "data": { "ticker": "NVDA", "side": "buy", "quantity": 5.0, "price": 121.4, "total": 607.0 } },
    { "type": "trade",  "status": "error", "detail": "Insufficient cash: need $50,000.00, have $9,393.00", "data": { "ticker": "TSLA", "side": "buy", "quantity": 200.0 } },
    { "type": "watchlist", "status": "ok", "detail": "Added PYPL to the watchlist", "data": { "ticker": "PYPL", "action": "add" } }
  ],
  "created_at": "2026-09-09T14:32:05.123456Z"
}
```

- `actions` is always present, `[]` when the model did nothing but talk.
- `type` is `"trade" | "watchlist"`; `status` is `"ok" | "error"`.
- **For a failed action, `detail` is the `TradeError`/`DbError` `message` verbatim** —
  do not re-phrase it. The illustrative strings in the example above are not
  normative; the real text comes from the layer that raised the error, so that one
  condition has exactly one wording everywhere it surfaces. (The data layer's
  actual messages are `"Need $X but only $Y available."` and
  `"Cannot sell N TICKER — only M held."`)
- Actions appear in execution order and render inline in the chat bubble as
  confirmation chips (green for ok, red for error).
- Failed actions do **not** fail the request — a 200 with an `error` action is the
  normal path for "the model asked for something impossible".
- A `502 LLM_ERROR` is returned only when the upstream call itself fails or the
  response cannot be parsed after retry.

### `GET /api/chat/history?limit=50`

**200**
```json
{
  "messages": [
    { "id": "…", "role": "user",      "content": "Buy 5 NVDA", "actions": null, "created_at": "…" },
    { "id": "…", "role": "assistant", "content": "Bought 5 NVDA…", "actions": [ "…" ], "created_at": "…" }
  ]
}
```
Oldest first. `actions` is `null` for user messages and an array (possibly empty)
for assistant messages. The frontend calls this once on mount to restore the
conversation.

---

## 6. LLM structured output schema

The model is called with `response_format` bound to this Pydantic model. This is
the **model-facing** schema — distinct from the `/api/chat` response above, which
is what the backend returns after executing the actions.

```python
class ChatTrade(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float

class ChatWatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]

class ChatResponse(BaseModel):
    message: str
    trades: list[ChatTrade] = []
    watchlist_changes: list[ChatWatchlistChange] = []
```

Execution order: **trades first, then watchlist changes**, each in array order.
Each trade goes through the exact same validation path as a manual
`POST /api/portfolio/trade` — one shared service function, not a parallel
implementation.

### Mock mode

When `LLM_MOCK=true` the backend must not call OpenRouter at all. It returns
deterministic responses driven by keyword matching on the user's message, so the
E2E suite can exercise every branch:

| User message contains (case-insensitive) | Mock returns |
|---|---|
| `"buy"` + a ticker + a number | `message` naming the trade, `trades: [that buy]` |
| `"sell"` + a ticker + a number | `message` naming the trade, `trades: [that sell]` |
| `"watch"` / `"add"` + a ticker | `watchlist_changes: [{add}]` |
| `"remove"` / `"unwatch"` + a ticker | `watchlist_changes: [{remove}]` |
| anything else | A fixed analysis string that includes the live cash balance and position count, `trades: []`, `watchlist_changes: []` |

The exact mock strings are owned by the LLM Engineer and must be documented in
`planning/LLM_NOTES.md` so the Integration Tester can assert on them.

---

## 7. Shared trade service (backend-internal, FROZEN)

The trade path must exist exactly once. Both `POST /api/portfolio/trade` and the
LLM chat auto-executor call this same function. It is owned and implemented by the
**Backend API Engineer**; the **LLM Engineer** imports it.

```python
# backend/app/services/trading.py

class TradeError(Exception):
    """Base for trade validation failures. Carries a code and a message."""
    code: str       # one of the API_CONTRACT §0 error codes
    message: str    # human-readable, safe to show a user or feed back to the LLM

def execute_trade(
    ticker: str,
    side: Literal["buy", "sell"],
    quantity: float,
    *,
    price_cache: PriceCache,
    user_id: str = "default",
) -> TradeResult: ...
```

- Raises `TradeError` (never `HTTPException`) so it is usable outside a request
  context. The router maps `TradeError` onto the HTTP error envelope; the chat
  executor turns it into an `{"status": "error"}` action and still returns 200.
- Normalises and validates the ticker, validates quantity and side, prices from
  `price_cache`, and delegates persistence to `db.execute_trade_atomic`.
- Adds the ticker to the market data source when it is not already tracked, so a
  newly bought position is priced.
- `TradeResult` exposes `.trade` (the `trade` object from §3) and `.portfolio`
  (the full `GET /api/portfolio` object, post-trade), which is exactly what the
  trade endpoint returns.

Until this module exists, the LLM Engineer mocks
`app.services.trading.execute_trade` at this import path in their tests.

## 8. Static file serving

The Backend API Engineer mounts the built frontend from `/app/static` in the
container — i.e. a `static/` directory at the **backend package root**, sibling to
`app/`, overridable via the `FINALLY_STATIC_DIR` environment variable. The DevOps
Engineer's Dockerfile copies the Next.js `out/` directory there. When the
directory is absent (local backend-only development) the app must still start,
serving the API and logging a single warning — not crashing.

Precedence rules for the catch-all mount:

- **`/api/*` always wins.** A path under `/api/` that no router claimed returns the
  JSON error envelope with a 404 — never the frontend's HTML. A client that
  mistypes an endpoint must get a parseable error, not a page that makes
  `response.json()` throw.
- **An unknown non-API path returns Next's `404.html` with a 404 status.** An
  earlier revision of this section called for `index.html` with a 200, the
  reflexive SPA-fallback rule. That was wrong for this app: the frontend is a
  static export with no client-side routes, so there are no deep links to rescue,
  and a real 404 for a genuinely missing page is more honest than a 200 serving
  the dashboard.
