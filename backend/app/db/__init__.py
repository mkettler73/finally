"""FinAlly data layer — the only module that touches SQLite.

Consumers import exclusively from here:

    from app.db import get_portfolio_state, execute_trade_atomic, ...

Nothing outside `app/db/` may import `sqlite3` or write SQL. The full contract
is `planning/DATA_LAYER.md`.

Public surface:
    init_db, reset_db_for_tests, utc_now_iso
    get_cash_balance, set_cash_balance
    list_watchlist, add_to_watchlist, remove_from_watchlist
    list_positions, get_position
    execute_trade_atomic, list_trades
    record_snapshot, list_snapshots
    append_chat_message, list_chat_messages
    Row types: WatchlistRow, PositionRow, TradeRow, SnapshotRow, ChatRow
    Errors: DbError, InsufficientCashError, InsufficientSharesError,
            DuplicateTickerError, TickerNotFoundError, WatchlistFullError
"""

from .connection import (
    get_db_path,
    init_db,
    read_transaction,
    reset_db_for_tests,
    utc_now_iso,
)
from .repository import (
    DEFAULT_USER_ID,
    QUANTITY_EPSILON,
    WATCHLIST_MAX,
    ChatRow,
    DbError,
    DuplicateTickerError,
    InsufficientCashError,
    InsufficientSharesError,
    PositionRow,
    SnapshotRow,
    TickerNotFoundError,
    TradeRow,
    WatchlistFullError,
    WatchlistRow,
    add_to_watchlist,
    append_chat_message,
    execute_trade_atomic,
    get_cash_balance,
    get_position,
    list_chat_messages,
    list_positions,
    list_snapshots,
    list_trades,
    list_watchlist,
    record_snapshot,
    remove_from_watchlist,
    set_cash_balance,
)
from .seed import DEFAULT_WATCHLIST, STARTING_CASH

__all__ = [
    "read_transaction",
    # Lifecycle
    "init_db",
    "reset_db_for_tests",
    "get_db_path",
    "utc_now_iso",
    # Profile
    "get_cash_balance",
    "set_cash_balance",
    # Watchlist
    "list_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    # Positions
    "list_positions",
    "get_position",
    # Trades
    "execute_trade_atomic",
    "list_trades",
    # Snapshots
    "record_snapshot",
    "list_snapshots",
    # Chat
    "append_chat_message",
    "list_chat_messages",
    # Row types
    "WatchlistRow",
    "PositionRow",
    "TradeRow",
    "SnapshotRow",
    "ChatRow",
    # Errors
    "DbError",
    "InsufficientCashError",
    "InsufficientSharesError",
    "DuplicateTickerError",
    "TickerNotFoundError",
    "WatchlistFullError",
    # Constants
    "DEFAULT_USER_ID",
    "DEFAULT_WATCHLIST",
    "STARTING_CASH",
    "WATCHLIST_MAX",
    "QUANTITY_EPSILON",
]
