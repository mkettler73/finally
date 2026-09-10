"""Default seed data for a fresh database (DATA_LAYER.md §3).

Seeding is additive and idempotent: each block inserts only when its table is
empty for that user, so re-running against a live database never resurrects a
ticker the user deleted or resets their cash.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from .connection import utc_now_iso

DEFAULT_USER_ID = "default"
STARTING_CASH = 10000.0

# Order matters: /api/watchlist sorts by added_at and the UI order must be stable.
DEFAULT_WATCHLIST = (
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
)


def _stepped_timestamps(count: int) -> list[str]:
    """`count` ISO timestamps, each strictly later than the last.

    Windows' system clock granularity is coarse enough (~15ms) that consecutive
    `utc_now_iso()` calls return the *same* string, which would make the seeded
    watchlist order depend on rowid ties. Stepping by a microsecond guarantees
    the ordering the contract promises.
    """
    base = datetime.now(UTC)
    return [
        (base + timedelta(microseconds=i)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        for i in range(count)
    ]


def seed_defaults(conn: sqlite3.Connection, user_id: str = DEFAULT_USER_ID) -> None:
    """Insert the default profile, watchlist and opening snapshot if absent."""
    profile = conn.execute("SELECT 1 FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    if profile is None:
        conn.execute(
            "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (user_id, STARTING_CASH, utc_now_iso()),
        )

    watched = conn.execute(
        "SELECT COUNT(*) AS n FROM watchlist WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    if watched == 0:
        stamps = _stepped_timestamps(len(DEFAULT_WATCHLIST))
        conn.executemany(
            "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            [
                (str(uuid.uuid4()), user_id, ticker, stamp)
                for ticker, stamp in zip(DEFAULT_WATCHLIST, stamps, strict=True)
            ],
        )

    snapshots = conn.execute(
        "SELECT COUNT(*) AS n FROM portfolio_snapshots WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    if snapshots == 0:
        # One opening point so the P&L chart is never empty on first load.
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, STARTING_CASH, utc_now_iso()),
        )
