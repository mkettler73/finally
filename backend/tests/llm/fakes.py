"""In-memory stand-ins for the modules the chat layer depends on.

`app.db` and `app.services.trading` are owned by other agents. These fakes
implement the frozen signatures from DATA_LAYER.md section 4 and
API_CONTRACT.md section 7 with real behaviour and real exception classes -
deliberately not `MagicMock`, which would happily answer any attribute asked of
it and let a broken call site pass green.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Literal

from app.market import MarketDataSource


# --- Exceptions (DATA_LAYER.md section 5) ---------------------------------
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


# --- Trade service (API_CONTRACT.md section 7) ----------------------------
class TradeError(Exception):
    """Mirrors the frozen trade service error: a stable code plus a message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FakeTradeResult:
    trade: dict[str, Any]
    portfolio: dict[str, Any]


@dataclass
class FakeTrading:
    """Records every call and returns a contract-shaped `TradeResult`."""

    price: float = 100.0
    error: TradeError | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def execute_trade(
        self,
        ticker: str,
        side: Literal["buy", "sell"],
        quantity: float,
        *,
        price_cache: Any,
        user_id: str = "default",
    ) -> FakeTradeResult:
        self.calls.append(
            {
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "price_cache": price_cache,
                "user_id": user_id,
            }
        )
        if self.error is not None:
            raise self.error
        price = price_cache.get_price(ticker) if price_cache else None
        price = self.price if price is None else price
        return FakeTradeResult(
            trade={
                "id": f"trade-{len(self.calls)}",
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "price": price,
                "total": quantity * price,
                "executed_at": "2026-09-09T14:32:05.123456Z",
            },
            portfolio={"cash_balance": 0.0, "positions": []},
        )


# --- Data layer (DATA_LAYER.md section 4) ---------------------------------
@dataclass
class FakeDb:
    """A tiny in-memory repository with the frozen public surface."""

    cash_balance: float = 10_000.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    watchlist: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    chat: list[dict[str, Any]] = field(default_factory=list)
    watchlist_cap: int = 30

    # Profile
    def get_cash_balance(self, user_id: str = "default") -> float:
        return self.cash_balance

    def set_cash_balance(self, amount: float, user_id: str = "default") -> None:
        self.cash_balance = amount

    # Watchlist
    def list_watchlist(self, user_id: str = "default") -> list[dict[str, Any]]:
        return list(self.watchlist)

    def add_to_watchlist(self, ticker: str, user_id: str = "default") -> dict[str, Any]:
        if any(row["ticker"] == ticker for row in self.watchlist):
            raise DuplicateTickerError(f"{ticker} is already on the watchlist.")
        if len(self.watchlist) >= self.watchlist_cap:
            raise WatchlistFullError(f"The watchlist is full at {self.watchlist_cap} tickers.")
        row = {
            "id": f"wl-{len(self.watchlist) + 1}",
            "ticker": ticker,
            "added_at": f"2026-09-09T14:30:{len(self.watchlist):02d}.000000Z",
        }
        self.watchlist.append(row)
        return row

    def remove_from_watchlist(self, ticker: str, user_id: str = "default") -> None:
        for index, row in enumerate(self.watchlist):
            if row["ticker"] == ticker:
                del self.watchlist[index]
                return
        raise TickerNotFoundError(f"{ticker} is not on the watchlist.")

    # Positions
    def list_positions(self, user_id: str = "default") -> list[dict[str, Any]]:
        return list(self.positions)

    def get_position(self, ticker: str, user_id: str = "default") -> dict[str, Any] | None:
        for row in self.positions:
            if row["ticker"] == ticker:
                return row
        return None

    # Snapshots
    def record_snapshot(self, total_value: float, user_id: str = "default") -> None:
        self.snapshots.append(
            {"total_value": total_value, "recorded_at": "2026-09-09T14:30:00.000000Z"}
        )

    def list_snapshots(self, limit: int = 500, user_id: str = "default") -> list[dict[str, Any]]:
        return self.snapshots[-limit:]

    # Chat
    def append_chat_message(
        self,
        role: Literal["user", "assistant"],
        content: str,
        actions: list[dict] | None = None,
        user_id: str = "default",
    ) -> dict[str, Any]:
        row = {
            "id": f"msg-{len(self.chat) + 1}",
            "role": role,
            "content": content,
            "actions": actions,
            "created_at": f"2026-09-09T14:32:{len(self.chat):02d}.000000Z",
        }
        self.chat.append(row)
        return row

    def list_chat_messages(self, limit: int = 50, user_id: str = "default") -> list[dict[str, Any]]:
        return self.chat[-limit:]


class FakeSource(MarketDataSource):
    """A market data source that records lifecycle calls instead of making them."""

    def __init__(self, tickers: list[str] | None = None) -> None:
        self._tickers = list(tickers or [])
        self.added: list[str] = []
        self.removed: list[str] = []
        self.fail_on_add = False

    async def start(self, tickers: list[str]) -> None:
        self._tickers = self._normalize_all(tickers)

    async def stop(self) -> None:
        return None

    async def add_ticker(self, ticker: str) -> None:
        if self.fail_on_add:
            raise RuntimeError("data source unreachable")
        ticker = self.normalize_ticker(ticker)
        self.added.append(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = self.normalize_ticker(ticker)
        self.removed.append(ticker)
        if ticker in self._tickers:
            self._tickers.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)


# --- sys.modules installation --------------------------------------------

_DB_EXPORTS = (
    "get_cash_balance",
    "set_cash_balance",
    "list_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "list_positions",
    "get_position",
    "record_snapshot",
    "list_snapshots",
    "append_chat_message",
    "list_chat_messages",
)


def install_fakes(monkeypatch, db: FakeDb, trading: FakeTrading) -> None:
    """Publish the fakes at `app.db` and `app.services.trading` in `sys.modules`.

    `app.llm` resolves both through `importlib` at call time, so installing
    them here is enough for every call site - and `monkeypatch` removes them
    again afterwards, leaving the real modules untouched for other suites.
    """
    db_module = ModuleType("app.db")
    for name in _DB_EXPORTS:
        setattr(db_module, name, getattr(db, name))
    for exc in (
        DbError,
        InsufficientCashError,
        InsufficientSharesError,
        DuplicateTickerError,
        TickerNotFoundError,
        WatchlistFullError,
    ):
        setattr(db_module, exc.__name__, exc)

    trading_module = ModuleType("app.services.trading")
    trading_module.execute_trade = trading.execute_trade
    trading_module.TradeError = TradeError

    monkeypatch.setitem(sys.modules, "app.db", db_module)
    monkeypatch.setitem(sys.modules, "app.services.trading", trading_module)
