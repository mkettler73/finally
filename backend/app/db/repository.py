"""The typed data-access surface consumed by the API and LLM layers.

Everything the rest of the backend can do to the database is a function here.
Cost-basis arithmetic lives in this module rather than in the API layer so the
manual-trade path and the LLM-trade path cannot drift apart.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Literal, TypedDict

from .connection import (
    get_connection,
    init_db,
    reset_db_for_tests,
    transaction,
    utc_now_iso,
)

__all__ = [
    "DbError",
    "InsufficientCashError",
    "InsufficientSharesError",
    "DuplicateTickerError",
    "TickerNotFoundError",
    "WatchlistFullError",
    "WatchlistRow",
    "PositionRow",
    "TradeRow",
    "SnapshotRow",
    "ChatRow",
    "WATCHLIST_MAX",
    "QUANTITY_EPSILON",
    "init_db",
    "reset_db_for_tests",
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
]

DEFAULT_USER_ID = "default"
WATCHLIST_MAX = 30

# A position at or below this many shares is float residue from repeated
# buy/sell arithmetic, not a holding: the row is deleted instead of lingering at
# 1e-15 shares. Doubles as the tolerance on the cash/share sufficiency checks.
QUANTITY_EPSILON = 1e-9


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class DbError(Exception):
    """Base class for every error the data layer raises deliberately.

    The message is human-readable and safe to surface straight into the API
    error envelope's `message` field.
    """


class InsufficientCashError(DbError):
    """A buy costs more than the available cash balance."""


class InsufficientSharesError(DbError):
    """A sell exceeds the held quantity."""


class DuplicateTickerError(DbError):
    """The ticker is already on the watchlist."""


class TickerNotFoundError(DbError):
    """The ticker is not on the watchlist."""


class WatchlistFullError(DbError):
    """The watchlist is at its cap."""


# --------------------------------------------------------------------------- #
# Row types
# --------------------------------------------------------------------------- #


class WatchlistRow(TypedDict):
    id: str
    ticker: str
    added_at: str


class PositionRow(TypedDict):
    id: str
    ticker: str
    quantity: float
    avg_cost: float
    updated_at: str


class TradeRow(TypedDict):
    id: str
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    executed_at: str


class SnapshotRow(TypedDict):
    total_value: float
    recorded_at: str


class ChatRow(TypedDict):
    id: str
    role: Literal["user", "assistant"]
    content: str
    actions: list[dict] | None
    created_at: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_ticker(ticker: str) -> str:
    """Uppercase and strip.

    The API layer normalises too; this is belt and braces so a stray lowercase
    ticker can never slip past `UNIQUE(user_id, ticker)`.
    """
    return ticker.strip().upper()


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _shares(quantity: float) -> str:
    text = f"{quantity:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _watchlist_row(row: sqlite3.Row) -> WatchlistRow:
    return WatchlistRow(id=row["id"], ticker=row["ticker"], added_at=row["added_at"])


def _position_row(row: sqlite3.Row) -> PositionRow:
    return PositionRow(
        id=row["id"],
        ticker=row["ticker"],
        quantity=float(row["quantity"]),
        avg_cost=float(row["avg_cost"]),
        updated_at=row["updated_at"],
    )


def _trade_row(row: sqlite3.Row) -> TradeRow:
    return TradeRow(
        id=row["id"],
        ticker=row["ticker"],
        side=row["side"],
        quantity=float(row["quantity"]),
        price=float(row["price"]),
        executed_at=row["executed_at"],
    )


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #


def get_cash_balance(user_id: str = DEFAULT_USER_ID) -> float:
    """Available cash. Raises DbError if the profile row is missing."""
    row = (
        get_connection()
        .execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,))
        .fetchone()
    )
    if row is None:
        raise DbError(f"No user profile for '{user_id}'.")
    return float(row["cash_balance"])


def set_cash_balance(amount: float, user_id: str = DEFAULT_USER_ID) -> None:
    """Overwrite the cash balance.

    Trades must not use this — they go through `execute_trade_atomic`, which
    moves cash and position together.
    """
    cursor = get_connection().execute(
        "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (float(amount), user_id)
    )
    if cursor.rowcount == 0:
        raise DbError(f"No user profile for '{user_id}'.")


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #


def list_watchlist(user_id: str = DEFAULT_USER_ID) -> list[WatchlistRow]:
    """Oldest first by `added_at`, rowid breaking any tie."""
    rows = (
        get_connection()
        .execute(
            "SELECT id, ticker, added_at FROM watchlist WHERE user_id = ?"
            " ORDER BY added_at ASC, rowid ASC",
            (user_id,),
        )
        .fetchall()
    )
    return [_watchlist_row(row) for row in rows]


def add_to_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> WatchlistRow:
    """Add a ticker. Raises DuplicateTickerError / WatchlistFullError.

    The duplicate check, the cap check and the insert share one transaction so
    two concurrent adds cannot both squeeze past the cap.
    """
    symbol = _normalize_ticker(ticker)
    row: WatchlistRow = WatchlistRow(id=str(uuid.uuid4()), ticker=symbol, added_at=utc_now_iso())
    with transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, symbol)
        ).fetchone()
        if existing is not None:
            raise DuplicateTickerError(f"{symbol} is already on your watchlist.")

        count = conn.execute(
            "SELECT COUNT(*) AS n FROM watchlist WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        if count >= WATCHLIST_MAX:
            raise WatchlistFullError(
                f"Watchlist is full ({WATCHLIST_MAX} tickers). Remove one before adding {symbol}."
            )

        conn.execute(
            "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (row["id"], user_id, symbol, row["added_at"]),
        )
    return row


def remove_from_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> None:
    """Remove a ticker. Raises TickerNotFoundError if it is not watched."""
    symbol = _normalize_ticker(ticker)
    cursor = get_connection().execute(
        "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, symbol)
    )
    if cursor.rowcount == 0:
        raise TickerNotFoundError(f"{symbol} is not on your watchlist.")


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #


def list_positions(user_id: str = DEFAULT_USER_ID) -> list[PositionRow]:
    """All open positions, alphabetical by ticker.

    Callers that need them sorted by market value do that themselves — this
    layer has no prices.
    """
    rows = (
        get_connection()
        .execute(
            "SELECT id, ticker, quantity, avg_cost, updated_at FROM positions"
            " WHERE user_id = ? ORDER BY ticker ASC",
            (user_id,),
        )
        .fetchall()
    )
    return [_position_row(row) for row in rows]


def get_position(ticker: str, user_id: str = DEFAULT_USER_ID) -> PositionRow | None:
    """One position, or None if the ticker is not held."""
    row = (
        get_connection()
        .execute(
            "SELECT id, ticker, quantity, avg_cost, updated_at FROM positions"
            " WHERE user_id = ? AND ticker = ?",
            (user_id, _normalize_ticker(ticker)),
        )
        .fetchone()
    )
    return _position_row(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Trades
# --------------------------------------------------------------------------- #


def execute_trade_atomic(
    ticker: str,
    side: Literal["buy", "sell"],
    quantity: float,
    price: float,
    total_value_after: float,
    user_id: str = DEFAULT_USER_ID,
) -> TradeRow:
    """Apply a validated trade in ONE transaction.

    Atomically: moves cash, upserts the position (weighted-average cost on buy,
    quantity reduction with `avg_cost` untouched on sell, DELETE when the
    remainder is float residue), appends the `trades` row, and writes a
    `portfolio_snapshots` row.

    The cash / share constraint is re-checked *inside* the transaction; the
    caller's pre-check only exists to produce a nicer error earlier. Violations
    raise InsufficientCashError / InsufficientSharesError and roll everything
    back, so a rejected trade leaves no cash movement, no trade row and no
    snapshot behind.

    `total_value_after` is supplied by the caller because valuing the portfolio
    needs the price cache, which this layer must not know about.
    """
    symbol = _normalize_ticker(ticker)
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if not quantity > 0:
        raise ValueError(f"quantity must be positive, got {quantity!r}")
    if price < 0:
        raise ValueError(f"price must not be negative, got {price!r}")

    notional = quantity * price
    trade: TradeRow = TradeRow(
        id=str(uuid.uuid4()),
        ticker=symbol,
        side=side,
        quantity=float(quantity),
        price=float(price),
        executed_at=utc_now_iso(),
    )

    with transaction() as conn:
        profile = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
        if profile is None:
            raise DbError(f"No user profile for '{user_id}'.")
        cash = float(profile["cash_balance"])

        position = conn.execute(
            "SELECT id, quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, symbol),
        ).fetchone()
        held = float(position["quantity"]) if position is not None else 0.0
        avg_cost = float(position["avg_cost"]) if position is not None else 0.0

        if side == "buy":
            if cash + QUANTITY_EPSILON < notional:
                raise InsufficientCashError(
                    f"Need {_money(notional)} but only {_money(cash)} available."
                )
            new_cash = cash - notional
            new_quantity = held + quantity
            # Weighted average: (old_qty*old_avg + qty*price) / (old_qty + qty)
            new_avg_cost = (held * avg_cost + quantity * price) / new_quantity
        else:
            if held + QUANTITY_EPSILON < quantity:
                raise InsufficientSharesError(
                    f"Cannot sell {_shares(quantity)} {symbol} — only {_shares(held)} held."
                )
            new_cash = cash + notional
            new_quantity = held - quantity
            new_avg_cost = avg_cost  # A sell never changes the cost basis.

        conn.execute("UPDATE users_profile SET cash_balance = ? WHERE id = ?", (new_cash, user_id))

        if new_quantity <= QUANTITY_EPSILON:
            # Fully closed. Deleting beats leaving a 1e-15-share ghost behind.
            if position is not None:
                conn.execute("DELETE FROM positions WHERE id = ?", (position["id"],))
        elif position is not None:
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? WHERE id = ?",
                (new_quantity, new_avg_cost, trade["executed_at"], position["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    symbol,
                    new_quantity,
                    new_avg_cost,
                    trade["executed_at"],
                ),
            )

        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                trade["id"],
                user_id,
                symbol,
                side,
                trade["quantity"],
                trade["price"],
                trade["executed_at"],
            ),
        )

        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, float(total_value_after), trade["executed_at"]),
        )

    return trade


def list_trades(limit: int = 100, user_id: str = DEFAULT_USER_ID) -> list[TradeRow]:
    """Most recent first."""
    rows = (
        get_connection()
        .execute(
            "SELECT id, ticker, side, quantity, price, executed_at FROM trades"
            " WHERE user_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT ?",
            (user_id, int(limit)),
        )
        .fetchall()
    )
    return [_trade_row(row) for row in rows]


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #


def record_snapshot(total_value: float, user_id: str = DEFAULT_USER_ID) -> None:
    """Append a portfolio value point.

    Called by the 30-second background task; the trade path writes its own
    snapshot inside the trade transaction instead of calling this.
    """
    get_connection().execute(
        "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
        " VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, float(total_value), utc_now_iso()),
    )


def list_snapshots(limit: int = 500, user_id: str = DEFAULT_USER_ID) -> list[SnapshotRow]:
    """The most recent `limit` snapshots, returned OLDEST FIRST.

    The P&L chart plots them left to right without re-sorting.
    """
    rows = (
        get_connection()
        .execute(
            "SELECT total_value, recorded_at FROM ("
            "  SELECT total_value, recorded_at, rowid AS rid FROM portfolio_snapshots"
            "  WHERE user_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?"
            ") ORDER BY recorded_at ASC, rid ASC",
            (user_id, int(limit)),
        )
        .fetchall()
    )
    return [
        SnapshotRow(total_value=float(row["total_value"]), recorded_at=row["recorded_at"])
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #


def append_chat_message(
    role: Literal["user", "assistant"],
    content: str,
    actions: list[dict] | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> ChatRow:
    """Append one message. `actions` is JSON-serialised; NULL when None."""
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")

    row: ChatRow = ChatRow(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        actions=actions,
        created_at=utc_now_iso(),
    )
    get_connection().execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            user_id,
            role,
            content,
            None if actions is None else json.dumps(actions),
            row["created_at"],
        ),
    )
    return row


def list_chat_messages(limit: int = 50, user_id: str = DEFAULT_USER_ID) -> list[ChatRow]:
    """The most recent `limit` messages, oldest first.

    `actions` comes back deserialised — consumers never see the raw JSON string.
    """
    rows = (
        get_connection()
        .execute(
            "SELECT id, role, content, actions, created_at FROM ("
            "  SELECT id, role, content, actions, created_at, rowid AS rid FROM chat_messages"
            "  WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?"
            ") ORDER BY created_at ASC, rid ASC",
            (user_id, int(limit)),
        )
        .fetchall()
    )
    return [
        ChatRow(
            id=row["id"],
            role=row["role"],
            content=row["content"],
            actions=None if row["actions"] is None else json.loads(row["actions"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]
