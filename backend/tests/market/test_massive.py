"""Tests for MassiveDataSource (mocked — no network).

Fixtures are built with the real ``massive`` model classes rather than bare
MagicMocks. This matters: a bare MagicMock auto-creates whatever attribute is
asked of it, so it will happily answer ``snap.last_trade.timestamp`` — a field
that does not exist on the real ``LastTrade`` — and certify a broken client as
working. See planning/MARKET_DATA_REVIEW.md finding #3.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from massive.exceptions import BadResponse
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import (
    GROUPED_DAILY_INTERVAL,
    MassiveDataSource,
    _extract_price,
    _is_plan_or_auth_error,
)

# A real SIP timestamp: Unix NANOSECONDS (19 digits), Nov 2020.
SIP_NS = 1605192894630916600
SIP_SECONDS = 1605192894.6309166


def _snapshot(ticker: str, *, price=None, sip_ns=None, prev_close=None) -> TickerSnapshot:
    """Build a TickerSnapshot exactly as the Massive client would parse one."""
    payload: dict = {"ticker": ticker}
    if price is not None:
        payload["lastTrade"] = {"p": price, "t": sip_ns}
    if prev_close is not None:
        payload["prevDay"] = {"c": prev_close}
    return TickerSnapshot.from_dict(payload)


def _source(cache: PriceCache, **kwargs) -> MassiveDataSource:
    source = MassiveDataSource(api_key="test-key", price_cache=cache, **kwargs)
    source._client = MagicMock()  # satisfy the _poll_once guard; never called
    return source


class TestExtractPrice:
    """Unit tests for the snapshot parsing rules (MASSIVE_API.md section 4)."""

    def test_prefers_last_trade(self):
        snap = _snapshot("AAPL", price=190.5, sip_ns=SIP_NS, prev_close=188.0)
        price, ts = _extract_price(snap)
        assert price == 190.5
        assert ts == pytest.approx(SIP_SECONDS)

    def test_converts_nanoseconds_to_seconds(self):
        """sip_timestamp is nanoseconds; dividing by 1000 lands in the year 50,832."""
        snap = _snapshot("AAPL", price=190.5, sip_ns=SIP_NS)
        _, ts = _extract_price(snap)
        assert ts == pytest.approx(SIP_SECONDS)
        # Sanity: a plausible Unix timestamp in seconds, not ms/us/ns.
        assert 1_000_000_000 < ts < 3_000_000_000

    def test_falls_back_to_prev_close_when_no_trade(self):
        """Overnight, at weekends and pre-open, lastTrade is absent."""
        snap = _snapshot("AAPL", prev_close=188.0)
        price, ts = _extract_price(snap)
        assert price == 188.0
        assert ts == pytest.approx(time.time(), abs=5)

    def test_falls_back_when_trade_has_no_price(self):
        snap = _snapshot("AAPL", price=None, prev_close=188.0)
        price, _ = _extract_price(snap)
        assert price == 188.0

    def test_missing_sip_timestamp_uses_wall_clock(self):
        snap = _snapshot("AAPL", price=190.5, sip_ns=None)
        price, ts = _extract_price(snap)
        assert price == 190.5
        assert ts == pytest.approx(time.time(), abs=5)

    def test_returns_none_when_unusable(self):
        assert _extract_price(_snapshot("AAPL")) is None


class TestPlanErrorDetection:
    """Distinguish 'your plan cannot do this' from transient failures."""

    @pytest.mark.parametrize(
        "message",
        [
            "401 Client Error: Unauthorized",
            "403 Client Error: Forbidden",
            '{"status":"NOT_AUTHORIZED","message":"not entitled to this endpoint"}',
        ],
    )
    def test_detects_plan_or_auth_errors(self, message):
        assert _is_plan_or_auth_error(BadResponse(message)) is True

    @pytest.mark.parametrize("message", ["429 Too Many Requests", "500 Internal Server Error"])
    def test_ignores_transient_errors(self, message):
        assert _is_plan_or_auth_error(BadResponse(message)) is False


@pytest.mark.asyncio
class TestMassiveDataSource:
    """Behaviour of the polling data source with the API mocked out."""

    async def test_poll_updates_cache(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL", "GOOGL"]

        snapshots = [
            _snapshot("AAPL", price=190.50, sip_ns=SIP_NS),
            _snapshot("GOOGL", price=175.25, sip_ns=SIP_NS),
        ]
        with patch.object(source, "_fetch_snapshots", return_value=snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_poll_stores_seconds_not_nanoseconds(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(
            source, "_fetch_snapshots", return_value=[_snapshot("AAPL", price=190.5, sip_ns=SIP_NS)]
        ):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.timestamp == pytest.approx(SIP_SECONDS)

    async def test_overnight_snapshot_still_fills_cache(self):
        """The common case for a demo app: run it outside US market hours."""
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(
            source, "_fetch_snapshots", return_value=[_snapshot("AAPL", prev_close=188.0)]
        ):
            await source._poll_once()

        assert cache.get_price("AAPL") == 188.0

    async def test_unusable_snapshot_skipped_without_losing_the_rest(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL", "BAD"]

        snapshots = [_snapshot("AAPL", price=190.50, sip_ns=SIP_NS), _snapshot("BAD")]
        with patch.object(source, "_fetch_snapshots", return_value=snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None

    async def test_api_error_does_not_crash(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # must not raise

        assert cache.get_price("AAPL") is None

    async def test_stale_prices_survive_a_failed_poll(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(
            source, "_fetch_snapshots", return_value=[_snapshot("AAPL", price=190.5, sip_ns=SIP_NS)]
        ):
            await source._poll_once()
        with patch.object(source, "_fetch_snapshots", side_effect=Exception("boom")):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50  # kept, not cleared

    async def test_one_api_call_regardless_of_ticker_count(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = [f"T{i}" for i in range(50)]

        with patch.object(source, "_fetch_snapshots", return_value=[]) as fetch:
            await source._poll_once()

        fetch.assert_called_once()

    async def test_empty_tickers_skips_poll(self):
        cache = PriceCache()
        source = _source(cache)
        source._tickers = []

        with patch.object(source, "_fetch_snapshots") as fetch:
            await source._poll_once()

        fetch.assert_not_called()

    # --- Ticker normalisation (MARKET_INTERFACE.md section 3, obligation 5) ---

    async def test_start_normalizes_tickers(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start([" tsla ", "aapl"])

        assert source.get_tickers() == ["TSLA", "AAPL"]
        await source.stop()

    async def test_start_deduplicates(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL", "aapl", " AAPL "])

        assert source.get_tickers() == ["AAPL"]
        await source.stop()

    async def test_add_ticker_normalizes(self):
        source = _source(PriceCache())
        await source.add_ticker("  aapl  ")
        assert source.get_tickers() == ["AAPL"]

    async def test_add_duplicate_is_noop(self):
        source = _source(PriceCache())
        await source.add_ticker("AAPL")
        await source.add_ticker("aapl")
        assert source.get_tickers() == ["AAPL"]

    async def test_remove_ticker_normalizes_and_evicts_cache(self):
        cache = PriceCache()
        source = _source(cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker(" aapl ")

        assert source.get_tickers() == ["GOOGL"]
        assert cache.get("AAPL") is None

    async def test_add_ticker_does_not_mutate_list_in_place(self):
        """_fetch_snapshots may be iterating _tickers in a worker thread."""
        source = _source(PriceCache())
        source._tickers = ["AAPL"]
        held = source._tickers

        await source.add_ticker("GOOGL")

        assert held == ["AAPL"]  # the snapshot a worker thread holds is unchanged
        assert source.get_tickers() == ["AAPL", "GOOGL"]

    # --- Lifecycle ---

    async def test_start_polls_immediately(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        snapshots = [_snapshot("AAPL", price=190.50, sip_ns=SIP_NS)]
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=snapshots):
                await source.start(["AAPL"])

        assert cache.get_price("AAPL") == 190.50
        await source.stop()

    async def test_stop_cancels_task(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=10.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        assert source._task is not None and not source._task.done()
        await source.stop()
        assert source._task is None

    async def test_stop_is_idempotent_and_safe_before_start(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        await source.stop()
        await source.stop()

    async def test_get_tickers_returns_a_copy(self):
        source = _source(PriceCache())
        source._tickers = ["AAPL"]
        source.get_tickers().append("MUTATED")
        assert source.get_tickers() == ["AAPL"]

    # --- Free-tier degradation (MARKET_INTERFACE.md section 6) ---

    async def test_plan_error_degrades_to_grouped_daily(self):
        """A free Basic key cannot call snapshots at all."""
        cache = PriceCache()
        source = _source(cache, poll_interval=15.0)
        source._tickers = ["AAPL"]

        with patch.object(
            source, "_fetch_snapshots", side_effect=BadResponse("403 NOT_AUTHORIZED")
        ):
            with patch.object(source, "_fetch_grouped_daily", return_value={"AAPL": 188.0}):
                await source._poll_once()

        assert source._mode == "grouped_daily"
        assert source._interval == GROUPED_DAILY_INTERVAL
        assert cache.get_price("AAPL") == 188.0

    async def test_degraded_mode_stops_calling_snapshots(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=15.0)
        source._tickers = ["AAPL"]
        source._mode = "grouped_daily"

        with patch.object(source, "_fetch_snapshots") as snapshots:
            with patch.object(source, "_fetch_grouped_daily", return_value={"AAPL": 188.0}):
                await source._poll_once()

        snapshots.assert_not_called()

    async def test_transient_error_does_not_degrade(self):
        """A 429 is backpressure, not a plan limit — stay on the snapshot path."""
        cache = PriceCache()
        source = _source(cache, poll_interval=15.0)
        source._tickers = ["AAPL"]

        with patch.object(source, "_fetch_snapshots", side_effect=BadResponse("429 rate limited")):
            await source._poll_once()

        assert source._mode == "snapshot"
        assert source._interval == 15.0

    async def test_grouped_daily_walks_back_past_non_trading_days(self):
        cache = PriceCache()
        source = _source(cache)
        source._tickers = ["AAPL"]
        client = MagicMock()
        # Two empty days (a weekend), then a real session.
        client.get_grouped_daily_aggs.side_effect = [
            [],
            [],
            [MagicMock(ticker="AAPL", close=188.0)],
        ]
        source._client = client

        closes = source._fetch_grouped_daily()

        assert closes == {"AAPL": 188.0}
        assert client.get_grouped_daily_aggs.call_count == 3

    async def test_grouped_daily_filters_to_watched_tickers(self):
        cache = PriceCache()
        source = _source(cache)
        source._tickers = ["AAPL"]
        client = MagicMock()
        client.get_grouped_daily_aggs.return_value = [
            MagicMock(ticker="AAPL", close=188.0),
            MagicMock(ticker="ZZZZ", close=1.0),
        ]
        source._client = client

        assert source._fetch_grouped_daily() == {"AAPL": 188.0}

    async def test_plan_error_is_logged_once_not_every_poll(self, caplog):
        """Otherwise a free key spams an error every 15s forever."""
        cache = PriceCache()
        source = _source(cache, poll_interval=15.0)
        source._tickers = ["AAPL"]

        with caplog.at_level("WARNING", logger="app.market.massive_client"):
            with patch.object(
                source, "_fetch_snapshots", side_effect=BadResponse("403 NOT_AUTHORIZED")
            ):
                with patch.object(source, "_fetch_grouped_daily", return_value={"AAPL": 188.0}):
                    await source._poll_once()
                    await source._poll_once()
                    await source._poll_once()

        degraded = [r for r in caplog.records if "Snapshots unavailable" in r.message]
        assert len(degraded) == 1
