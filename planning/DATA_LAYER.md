# FinAlly — Data Layer Contract (FROZEN)

The binding contract between the **Database Engineer** (who implements it) and the
**Backend API Engineer** and **LLM Engineer** (who consume it). The API layer must
never open a SQLite connection or write SQL of its own — every database touch goes
through `app.db`.

---

## 1. Module layout

```
backend/app/db/
├── __init__.py      # Re-exports the public surface below. This is the ONLY import path consumers use.
├── connection.py    # Connection management, lazy init, PRAGMA setup
├── schema.sql       # DDL, exactly as specified in PLAN.md §7
├── seed.py          # Default seed data
└── repository.py    # The typed functions in §4
```

Consumers import exclusively as:

```python
from app.db import get_portfolio_state, execute_trade_atomic, ...
```

Nothing outside `app/db/` may import `sqlite3`.

---

## 2. Connection & initialisation

- Database path comes from the `FINALLY_DB_PATH` environment variable, defaulting
  to `db/finally.db` **relative to the project root** (the repo root, not
  `backend/`). In Docker this resolves to `/app/db/finally.db`, the volume mount.
  Create parent directories if missing.
- **Lazy initialisation:** on first connection, if the expected tables are absent,
  run `schema.sql` and seed. Must be safe to call repeatedly and safe under
  concurrent first-touch (guard with a lock).
- Required PRAGMAs on every connection:
  - `journal_mode=WAL` — the snapshot background task writes while requests read.
  - `foreign_keys=ON`
  - `busy_timeout=5000`
- `row_factory = sqlite3.Row` so rows are addressable by column name.
- SQLite is not safe to share across threads by default and FastAPI runs sync
  endpoints in a threadpool. Use **one connection per thread** (`threading.local`)
  or a small pool — do **not** create one global connection with
  `check_same_thread=False` and no locking.
- Expose `init_db()` (idempotent) for the app's lifespan startup, and
  `reset_db_for_tests()` used only by test fixtures.

---

## 3. Schema

Exactly as specified in `PLAN.md §7` — six tables: `users_profile`, `watchlist`,
`positions`, `trades`, `portfolio_snapshots`, `chat_messages`. Every table carries
`user_id TEXT NOT NULL DEFAULT 'default'`.

Additions permitted (and expected) beyond PLAN.md:
- `NOT NULL` constraints wherever a column is genuinely required.
- `CHECK` constraints: `side IN ('buy','sell')`, `role IN ('user','assistant')`,
  `quantity > 0` on trades, `quantity >= 0` on positions.
- Indexes: `trades(user_id, executed_at)`, `portfolio_snapshots(user_id, recorded_at)`,
  `chat_messages(user_id, created_at)`.
- The `UNIQUE(user_id, ticker)` constraints on `watchlist` and `positions` from
  PLAN.md are mandatory — the trade path relies on them for upsert correctness.

Seed data on a fresh database:
- `users_profile`: one row, `id='default'`, `cash_balance=10000.0`.
- `watchlist`: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX — inserted
  in that order with strictly increasing `added_at`, because `/api/watchlist` sorts
  by `added_at` and the UI order must be stable.
- `portfolio_snapshots`: one seed row, `total_value=10000.0`, so the P&L chart is
  never empty on first load.

---

## 4. Public function surface

All functions take `user_id: str = "default"` as their final parameter. All
timestamps written are ISO-8601 UTC with a `Z` suffix, produced by one shared
`utc_now_iso()` helper exported from `app.db`.

Return types are `TypedDict`s or dataclasses defined in `app/db/repository.py` and
re-exported — not bare dicts — so the API layer gets type checking.

### Profile
```python
def get_cash_balance(user_id: str = "default") -> float: ...
def set_cash_balance(amount: float, user_id: str = "default") -> None: ...
```

### Watchlist
```python
def list_watchlist(user_id: str = "default") -> list[WatchlistRow]:
    """Oldest first by added_at. WatchlistRow: {id, ticker, added_at}."""

def add_to_watchlist(ticker: str, user_id: str = "default") -> WatchlistRow:
    """Raises DuplicateTickerError if present, WatchlistFullError past 30."""

def remove_from_watchlist(ticker: str, user_id: str = "default") -> None:
    """Raises TickerNotFoundError if absent."""
```

### Positions
```python
def list_positions(user_id: str = "default") -> list[PositionRow]:
    """PositionRow: {id, ticker, quantity, avg_cost, updated_at}."""

def get_position(ticker: str, user_id: str = "default") -> PositionRow | None: ...
```

### Trades — the critical one
```python
def execute_trade_atomic(
    ticker: str,
    side: Literal["buy", "sell"],
    quantity: float,
    price: float,
    total_value_after: float,
    user_id: str = "default",
) -> TradeRow:
    """Apply a validated trade in ONE transaction.

    Performs, atomically:
      1. Debit/credit users_profile.cash_balance by quantity * price.
      2. Upsert positions (weighted-average cost on buy; quantity reduction on
         sell, avg_cost untouched; DELETE when the remaining quantity <= 1e-9).
      3. INSERT the trades row.
      4. INSERT a portfolio_snapshots row with total_value_after.

    Re-checks the cash / share constraint inside the transaction and raises
    InsufficientCashError / InsufficientSharesError on violation, rolling back.
    The caller's pre-check is for the error message; THIS check is the one that
    guarantees correctness under concurrency.

    total_value_after is supplied by the caller because valuing the portfolio
    requires the price cache, which the DB layer must not know about.
    """

def list_trades(limit: int = 100, user_id: str = "default") -> list[TradeRow]:
    """Most recent first."""
```

Cost-basis arithmetic lives **here**, not in the API layer, so the manual-trade and
LLM-trade paths cannot drift:
- Buy: `new_avg = (old_qty * old_avg + qty * price) / (old_qty + qty)`
- Sell: `avg_cost` unchanged; `new_qty = old_qty - qty`

### Snapshots
```python
def record_snapshot(total_value: float, user_id: str = "default") -> None: ...
def list_snapshots(limit: int = 500, user_id: str = "default") -> list[SnapshotRow]:
    """OLDEST FIRST after applying the limit — i.e. the most recent `limit`
    snapshots, returned in ascending time order. SnapshotRow: {total_value, recorded_at}."""
```

### Chat
```python
def append_chat_message(
    role: Literal["user", "assistant"],
    content: str,
    actions: list[dict] | None = None,
    user_id: str = "default",
) -> ChatRow:
    """actions is JSON-serialised into the TEXT column; NULL when None."""

def list_chat_messages(limit: int = 50, user_id: str = "default") -> list[ChatRow]:
    """Oldest first after applying the limit. actions is JSON-DESERIALISED back
    into list[dict] | None — consumers never see the raw JSON string."""
```

---

## 5. Exceptions

Defined in `app/db/repository.py`, re-exported from `app.db`:

```python
class DbError(Exception): ...
class InsufficientCashError(DbError): ...
class InsufficientSharesError(DbError): ...
class DuplicateTickerError(DbError): ...
class TickerNotFoundError(DbError): ...
class WatchlistFullError(DbError): ...
```

Each carries a human-readable message suitable for surfacing directly in the API
error envelope's `message` field. The API layer maps these onto the HTTP status
codes and `code` values in `API_CONTRACT.md §0` — the DB layer never imports
`fastapi` or raises `HTTPException`.

---

## 6. Testing obligations (Database Engineer)

`backend/tests/db/`, using a temp-file database per test (not `:memory:` — the
threading model must be exercised):

- Lazy init creates all six tables and seeds correctly; running it twice is a no-op.
- `execute_trade_atomic` weighted-average cost across a buy, a second buy at a
  different price, and a partial sell.
- A sell that zeroes the position deletes the row.
- Insufficient cash and insufficient shares both raise **and roll back** — assert
  cash, positions, trades and snapshots are all unchanged after the failure.
- Concurrent trades from multiple threads never produce a negative cash balance.
- `list_snapshots` ordering and limit semantics (most recent N, ascending).
- `append_chat_message` / `list_chat_messages` round-trip `actions` through JSON,
  including the `None` case.
- Float precision: 100 small buys then one full sell leaves quantity at exactly 0
  and deletes the row (not a `1e-15` residue).
