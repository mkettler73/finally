"""A recording MarketDataSource for tests.

Built on the real ``MarketDataSource`` base class rather than a MagicMock: a
MagicMock invents whatever attribute is asked of it, which is how a broken
Massive client once passed thirteen green tests in this repo.
"""

from __future__ import annotations

from app.market import MarketDataSource, PriceCache


class FakeMarketSource(MarketDataSource):
    """Tracks tickers in memory and records every add/remove call."""

    def __init__(self, price_cache: PriceCache | None = None, tickers: list[str] | None = None):
        self.price_cache = price_cache
        self._tickers: list[str] = list(tickers or [])
        self.added: list[str] = []
        self.removed: list[str] = []
        self.started = False
        self.stopped = False

    async def start(self, tickers: list[str]) -> None:
        self._tickers = self._normalize_all(tickers)
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def add_ticker(self, ticker: str) -> None:
        symbol = self.normalize_ticker(ticker)
        self.added.append(symbol)
        if symbol not in self._tickers:
            self._tickers.append(symbol)

    async def remove_ticker(self, ticker: str) -> None:
        symbol = self.normalize_ticker(ticker)
        self.removed.append(symbol)
        if symbol in self._tickers:
            self._tickers.remove(symbol)
        if self.price_cache is not None:
            self.price_cache.remove(symbol)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)
