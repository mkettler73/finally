"""An in-memory stand-in for ``app.db``.

The API layer is built against the frozen surface in ``planning/DATA_LAYER.md``
while the real repository is written in parallel, so these tests install this
module in place of ``app.db``. It implements the same signatures, the same
exception types and the same cost-basis arithmetic, which keeps the service
tests honest about semantics without dragging a SQLite file into every test.

Installing it explicitly (rather than only when ``app.db`` is missing) means
these tests assert the API layer's behaviour, never the repository's.
"""

from __future__ import annotations

import itertools
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal


class DbError(Exception):
    pass


class InsufficientCashError(DbError):
    pass


class InsufficientSharesError(DbError):
    pass


class DuplicateTickerError(DbError):
    pass


class TickerNotFoundError(DbError):
    pass


class WatchlistFullError(DbError):
    pass


EXCEPTIONS = (
    DbError,
    InsufficientCashError,
    InsufficientSharesError,
    DuplicateTickerError,
    TickerNotFoundError,
    WatchlistFullError,
)

WATCHLIST_CAP = 30
DUST = 1e-9
STARTING_CASH = 10000.0
DEFAULT_WATCHLIST = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


class FakeDb:
    """A single-user in-memory implementation of the data layer contract."""

    def __init__(self, seed: bool = True) -> None:
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)
        self.cash: float = STARTING_CASH
        self.watchlist: list[dict[str, Any]] = []
        self.positions: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.chat: list[dict[str, Any]] = []
        self.init_calls = 0
        self.read_transactions = 0
        if seed:
            for ticker in DEFAULT_WATCHLIST:
                self.add_to_watchlist(ticker)
            self.record_snapshot(STARTING_CASH)

    # --- helpers -------------------------------------------------------

    def _id(self) -> str:
        return f"id-{next(self._ids)}"

    def utc_now_iso(self) -> str:
        # Strictly increasing so added_at / recorded_at ordering is testable.
        return datetime.fromtimestamp(next(self._clock), UTC).isoformat().replace("+00:00", "Z")

    # --- lifecycle -----------------------------------------------------

    def init_db(self) -> None:
        self.init_calls += 1

    def reset_db_for_tests(self) -> None:
        self.__init__()

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        """Stand-in for the real read snapshot.

        Single-threaded in tests, so there is nothing to isolate against; the
        counter exists so a caller can assert its reads were grouped. The real
        one wraps them in BEGIN DEFERRED so a trade committing mid-valuation
        cannot produce cash-after with positions-before.
        """
        self.read_transactions += 1
        yield

    # --- profile -------------------------------------------------------

    def get_cash_balance(self, user_id: str = "default") -> float:
        return self.cash

    def set_cash_balance(self, amount: float, user_id: str = "default") -> None:
        self.cash = float(amount)

    # --- watchlist -----------------------------------------------------

    def list_watchlist(self, user_id: str = "default") -> list[dict[str, Any]]:
        return [dict(row) for row in self.watchlist]

    def add_to_watchlist(self, ticker: str, user_id: str = "default") -> dict[str, Any]:
        if any(row["ticker"] == ticker for row in self.watchlist):
            raise DuplicateTickerError(f"{ticker} is already on the watchlist.")
        if len(self.watchlist) >= WATCHLIST_CAP:
            raise WatchlistFullError(f"Watchlist is full ({WATCHLIST_CAP} tickers).")
        row = {"id": self._id(), "ticker": ticker, "added_at": self.utc_now_iso()}
        self.watchlist.append(row)
        return dict(row)

    def remove_from_watchlist(self, ticker: str, user_id: str = "default") -> None:
        before = len(self.watchlist)
        self.watchlist = [row for row in self.watchlist if row["ticker"] != ticker]
        if len(self.watchlist) == before:
            raise TickerNotFoundError(f"{ticker} is not on the watchlist.")

    # --- positions -----------------------------------------------------

    def list_positions(self, user_id: str = "default") -> list[dict[str, Any]]:
        return [dict(row) for row in self.positions.values()]

    def get_position(self, ticker: str, user_id: str = "default") -> dict[str, Any] | None:
        row = self.positions.get(ticker)
        return dict(row) if row else None

    # --- trades --------------------------------------------------------

    def execute_trade_atomic(
        self,
        ticker: str,
        side: Literal["buy", "sell"],
        quantity: float,
        price: float,
        total_value_after: float,
        user_id: str = "default",
    ) -> dict[str, Any]:
        total = quantity * price
        existing = self.positions.get(ticker)

        if side == "buy":
            if total > self.cash:
                raise InsufficientCashError(
                    f"Need ${total:,.2f} but only ${self.cash:,.2f} available."
                )
            self.cash -= total
            old_qty = float(existing["quantity"]) if existing else 0.0
            old_avg = float(existing["avg_cost"]) if existing else 0.0
            new_qty = old_qty + quantity
            new_avg = (old_qty * old_avg + quantity * price) / new_qty
            self.positions[ticker] = {
                "id": existing["id"] if existing else self._id(),
                "ticker": ticker,
                "quantity": new_qty,
                "avg_cost": new_avg,
                "updated_at": self.utc_now_iso(),
            }
        else:
            held = float(existing["quantity"]) if existing else 0.0
            if quantity > held:
                raise InsufficientSharesError(f"Only {held:g} {ticker} held.")
            self.cash += total
            new_qty = held - quantity
            if new_qty <= DUST:
                self.positions.pop(ticker, None)
            else:
                self.positions[ticker] = {
                    **existing,
                    "quantity": new_qty,
                    "updated_at": self.utc_now_iso(),
                }

        row = {
            "id": self._id(),
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "executed_at": self.utc_now_iso(),
        }
        self.trades.append(row)
        self.record_snapshot(total_value_after)
        return dict(row)

    def list_trades(self, limit: int = 100, user_id: str = "default") -> list[dict[str, Any]]:
        return [dict(row) for row in reversed(self.trades)][:limit]

    # --- snapshots -----------------------------------------------------

    def record_snapshot(self, total_value: float, user_id: str = "default") -> None:
        self.snapshots.append(
            {"total_value": float(total_value), "recorded_at": self.utc_now_iso()}
        )

    def list_snapshots(self, limit: int = 500, user_id: str = "default") -> list[dict[str, Any]]:
        return [dict(row) for row in self.snapshots[-limit:]]

    # --- chat ----------------------------------------------------------

    def append_chat_message(
        self,
        role: Literal["user", "assistant"],
        content: str,
        actions: list[dict] | None = None,
        user_id: str = "default",
    ) -> dict[str, Any]:
        row = {
            "id": self._id(),
            "role": role,
            "content": content,
            "actions": actions,
            "created_at": self.utc_now_iso(),
        }
        self.chat.append(row)
        return dict(row)

    def list_chat_messages(self, limit: int = 50, user_id: str = "default") -> list[dict[str, Any]]:
        return [dict(row) for row in self.chat[-limit:]]


PUBLIC_FUNCTIONS = (
    "init_db",
    "reset_db_for_tests",
    "read_transaction",
    "utc_now_iso",
    "get_cash_balance",
    "set_cash_balance",
    "list_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "list_positions",
    "get_position",
    "execute_trade_atomic",
    "list_trades",
    "record_snapshot",
    "list_snapshots",
    "append_chat_message",
    "list_chat_messages",
)


def install_fake_db(monkeypatch, store: FakeDb | None = None) -> FakeDb:
    """Bind a FakeDb into ``sys.modules`` as ``app.db`` for one test."""
    import app

    store = store or FakeDb()
    module = types.ModuleType("app.db")
    for name in PUBLIC_FUNCTIONS:
        setattr(module, name, getattr(store, name))
    for exc in EXCEPTIONS:
        setattr(module, exc.__name__, exc)
    module.store = store  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.db", module)
    monkeypatch.setattr(app, "db", module, raising=False)
    return store
