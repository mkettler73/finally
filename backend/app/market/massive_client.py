"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

# Once degraded to end-of-day closes there is nothing new to fetch until the
# next session, so polling faster only burns the free tier's 5 calls/min.
GROUPED_DAILY_INTERVAL = 900.0

# Weekends and holidays return an empty result set rather than an error, so a
# grouped-daily fetch has to walk backwards to find the last real session.
MAX_LOOKBACK_DAYS = 7

# BadResponse is deliberately coarse — 401, 403 and 429 all surface as the same
# type — so the plan/auth cases have to be recognised from the message body.
_PLAN_OR_AUTH_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "not_authorized",
    "not authorized",
    "not entitled",
    "forbidden",
)


def _extract_price(snap) -> tuple[float, float] | None:
    """(price_in_dollars, timestamp_in_unix_seconds), or None if unusable.

    Prefers the last trade; falls back to the previous close so that overnight,
    pre-market and weekend sessions still show a sensible price.
    """
    trade = getattr(snap, "last_trade", None)
    if trade is not None and trade.price is not None:
        # NOTE: the attribute is sip_timestamp, NOT timestamp, and it is in
        # NANOSECONDS. See planning/MASSIVE_API.md section 4.
        ns = trade.sip_timestamp
        ts = ns / 1_000_000_000 if ns else time.time()
        return float(trade.price), float(ts)

    prev = getattr(snap, "prev_day", None)
    if prev is not None and prev.close is not None:
        return float(prev.close), time.time()

    return None


def _is_plan_or_auth_error(error: BaseException) -> bool:
    """True when the API is refusing the endpoint rather than failing transiently."""
    message = str(error).lower()
    return any(marker in message for marker in _PLAN_OR_AUTH_MARKERS)


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    A free Stocks Basic key cannot call snapshots at all. Rather than logging an
    error every 15s forever, the first plan/auth refusal switches the source to
    the grouped-daily endpoint, which every plan can call. Prices then become
    end-of-day closes rather than a live feed — degraded, but honest, and still
    enough for the portfolio, positions table and heatmap to be correct.

    Rate limits:
      - Free tier: 5 req/min -> snapshots unavailable, EOD closes every 900s
      - Paid tiers: higher limits -> poll every 2-15s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._mode: str = "snapshot"

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = self._normalize_all(tickers)

        # Do an immediate first poll so the cache has data right away
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self._client is not None:
            # Release the urllib3 connection pool rather than just dropping it.
            close = getattr(self._client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover - best effort cleanup
                    logger.debug("Ignoring error while closing Massive client", exc_info=True)
            self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = self.normalize_ticker(ticker)
        if ticker not in self._tickers:
            # Rebind rather than append: _fetch_snapshots may be iterating the
            # current list inside a worker thread.
            self._tickers = [*self._tickers, ticker]
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = self.normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            # Read the interval each pass: degrading to grouped daily widens it.
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch prices, update cache."""
        if not self._tickers or not self._client:
            return

        if self._mode == "snapshot":
            try:
                await self._poll_snapshot()
                return
            except BadResponse as e:
                if not _is_plan_or_auth_error(e):
                    logger.error("Massive poll failed: %s", e)
                    return
                logger.warning(
                    "Snapshots unavailable on this plan (%s); "
                    "falling back to end-of-day closes every %.0fs",
                    e,
                    GROUPED_DAILY_INTERVAL,
                )
                self._mode = "grouped_daily"
                self._interval = GROUPED_DAILY_INTERVAL
            except Exception as e:
                # Network errors, DNS, urllib3 — keep stale prices, retry later.
                logger.error("Massive poll failed: %s", e)
                return

        await self._poll_grouped_daily()

    async def _poll_snapshot(self) -> None:
        """Live(ish) path: one snapshot call covers every watched ticker."""
        # The Massive RESTClient is synchronous — run in a thread to avoid
        # blocking the event loop.
        snapshots = await asyncio.to_thread(self._fetch_snapshots)

        processed = 0
        for snap in snapshots:
            parsed = _extract_price(snap)
            if parsed is None:
                logger.warning("No usable price for %s", getattr(snap, "ticker", "???"))
                continue
            price, ts = parsed
            self._cache.update(ticker=snap.ticker, price=price, timestamp=ts)
            processed += 1

        logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))

    async def _poll_grouped_daily(self) -> None:
        """Degraded path: end-of-day closes, available on every plan."""
        try:
            closes = await asyncio.to_thread(self._fetch_grouped_daily)
        except Exception as e:
            logger.error("Massive grouped-daily poll failed: %s", e)
            return

        now = time.time()
        for ticker, close in closes.items():
            self._cache.update(ticker=ticker, price=close, timestamp=now)

        logger.debug(
            "Massive grouped daily: updated %d/%d tickers", len(closes), len(self._tickers)
        )

    def _fetch_snapshots(self) -> list:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )

    def _fetch_grouped_daily(self) -> dict[str, float]:
        """Closing price for every watched ticker, in one call per attempt.

        A non-trading date is not an error — it returns zero results — so walk
        backwards through the calendar to find the last real session.
        """
        wanted = set(self._tickers)
        day = date.today()

        for _ in range(MAX_LOOKBACK_DAYS):
            bars = self._client.get_grouped_daily_aggs(str(day), adjusted=True)
            closes = {b.ticker: b.close for b in bars if b.ticker in wanted}
            if closes:
                return closes
            day -= timedelta(days=1)

        logger.warning("No trading day with data found in the last %d days", MAX_LOOKBACK_DAYS)
        return {}
