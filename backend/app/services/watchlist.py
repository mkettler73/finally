"""Watchlist rows: a db entry joined to live prices (API_CONTRACT §4)."""

from __future__ import annotations

from typing import Any

from ..market import PriceCache
from .rows import row_to_dict
from .session_open import SessionOpenTracker


def build_watchlist_row(
    entry: Any,
    price_cache: PriceCache,
    session_opens: SessionOpenTracker,
) -> dict[str, Any]:
    """One `GET /api/watchlist` row.

    Price fields are null (and ``direction`` "flat") until the ticker has ticked
    at least once, which happens within the first cache cycle after it is added.
    """
    row = row_to_dict(entry)
    ticker = row["ticker"]
    update = price_cache.get(ticker)

    session_open = session_opens.get(ticker)
    price = update.price if update is not None else None

    session_change: float | None = None
    session_change_percent: float | None = None
    if price is not None and session_open is not None:
        session_change = price - session_open
        session_change_percent = session_change / session_open * 100 if session_open != 0 else 0.0

    return {
        "ticker": ticker,
        "price": price,
        "previous_price": update.previous_price if update is not None else None,
        "change": update.change if update is not None else None,
        "change_percent": update.change_percent if update is not None else None,
        "direction": update.direction if update is not None else "flat",
        "session_open": session_open,
        "session_change": session_change,
        "session_change_percent": session_change_percent,
        "added_at": row["added_at"],
    }
