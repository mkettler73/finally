"""Background task that records portfolio value over time for the P&L chart.

Trades write their own snapshot inside the trade transaction; this loop covers
the periods between trades, so an untouched portfolio still draws a line as
prices move.
"""

from __future__ import annotations

import asyncio
import logging

from ..market import PriceCache
from .portfolio import total_portfolio_value
from .session_open import SessionOpenTracker

logger = logging.getLogger(__name__)

# PLAN.md §7: a snapshot every 30 seconds. Injectable so tests never sleep.
SNAPSHOT_INTERVAL_SECONDS = 30.0


def record_snapshot_now(price_cache: PriceCache, user_id: str = "default") -> None:
    """Value the portfolio and append one `portfolio_snapshots` row."""
    from .. import db

    db.record_snapshot(total_portfolio_value(price_cache, user_id), user_id)


async def run_snapshot_loop(
    price_cache: PriceCache,
    *,
    interval: float = SNAPSHOT_INTERVAL_SECONDS,
    session_opens: SessionOpenTracker | None = None,
    user_id: str = "default",
) -> None:
    """Record a snapshot every ``interval`` seconds until cancelled.

    Runs the blocking valuation in a worker thread so the event loop keeps
    serving the SSE stream. A failure is logged and the loop continues: losing
    one chart point must not take the task down for the life of the process.
    """
    while True:
        try:
            await asyncio.sleep(interval)
            if session_opens is not None:
                session_opens.observe_cache(price_cache)
            await asyncio.to_thread(record_snapshot_now, price_cache, user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Snapshot task failed; continuing")
