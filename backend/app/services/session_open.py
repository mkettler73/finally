"""Session-open prices: the first price seen for a ticker since process start.

The market module deliberately knows nothing about sessions — its cache is
tick-over-tick only. The watchlist UI needs a "day change" figure, so the API
layer remembers the first price it ever observed for each ticker and reports
the move against it (API_CONTRACT §4).
"""

from __future__ import annotations

from threading import Lock

from ..market import PriceCache


class SessionOpenTracker:
    """First observed price per ticker, thread-safe, first write wins."""

    def __init__(self) -> None:
        self._opens: dict[str, float] = {}
        self._lock = Lock()

    def observe(self, ticker: str, price: float | None) -> None:
        """Record ``price`` as the session open unless one is already held."""
        if price is None:
            return
        with self._lock:
            self._opens.setdefault(ticker, price)

    def observe_cache(self, price_cache: PriceCache) -> None:
        """Record session opens for every ticker currently in the cache."""
        for ticker, update in price_cache.get_all().items():
            self.observe(ticker, update.price)

    def get(self, ticker: str) -> float | None:
        with self._lock:
            return self._opens.get(ticker)

    def forget(self, ticker: str) -> None:
        """Drop a ticker, so re-adding it starts a fresh session baseline."""
        with self._lock:
            self._opens.pop(ticker, None)

    def reset(self) -> None:
        with self._lock:
            self._opens.clear()
