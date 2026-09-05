"""Interface-conformance suite: one set of assertions, run against every source.

MARKET_INTERFACE.md section 10 calls this "the highest-value test in the
subsystem: it is what guarantees that swapping sources on MASSIVE_API_KEY cannot
change observable behaviour." Every obligation in section 3 is asserted here for
both implementations, so a source-specific divergence fails the build.

The Massive source is driven with a mocked REST client — no network.
"""

from unittest.mock import MagicMock, patch

import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource

SIP_NS = 1605192894630916600


class SourceHarness:
    """Builds a started source and whatever mocking it needs to run offline."""

    name: str

    def __init__(self, cache: PriceCache):
        self.cache = cache

    def make(self) -> MarketDataSource:
        raise NotImplementedError

    async def start(self, source: MarketDataSource, tickers: list[str]) -> None:
        raise NotImplementedError


class SimulatorHarness(SourceHarness):
    name = "simulator"

    def make(self) -> MarketDataSource:
        return SimulatorDataSource(price_cache=self.cache, update_interval=0.01)

    async def start(self, source, tickers):
        await source.start(tickers)


class MassiveHarness(SourceHarness):
    name = "massive"

    def make(self) -> MarketDataSource:
        return MassiveDataSource(api_key="test-key", price_cache=self.cache, poll_interval=60.0)

    async def start(self, source, tickers):
        # Return a snapshot for whatever the source asked for, so start() seeds
        # the cache exactly as a live API would.
        def fetch():
            return [
                TickerSnapshot.from_dict({"ticker": t, "lastTrade": {"p": 100.0, "t": SIP_NS}})
                for t in source.get_tickers()
            ]

        with patch("app.market.massive_client.RESTClient", MagicMock()):
            with patch.object(source, "_fetch_snapshots", side_effect=fetch):
                await source.start(tickers)


HARNESSES = [SimulatorHarness, MassiveHarness]


@pytest.fixture(params=HARNESSES, ids=[h.name for h in HARNESSES])
def harness(request) -> SourceHarness:
    return request.param(PriceCache())


@pytest.mark.asyncio
class TestMarketDataSourceContract:
    """The obligations every implementation owes (MARKET_INTERFACE.md section 3)."""

    async def test_is_a_market_data_source(self, harness):
        assert isinstance(harness.make(), MarketDataSource)

    async def test_start_seeds_the_cache_before_returning(self, harness):
        """A user opening the page must never see an empty watchlist."""
        source = harness.make()
        await harness.start(source, ["AAPL", "GOOGL"])

        assert harness.cache.get("AAPL") is not None
        assert harness.cache.get("GOOGL") is not None
        assert harness.cache.get_price("AAPL") > 0

        await source.stop()

    async def test_start_records_the_active_set(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL", "GOOGL"])

        assert set(source.get_tickers()) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_start_normalizes_tickers(self, harness):
        """Obligation 5: normalised to upper case, stripped, at the boundary."""
        source = harness.make()
        await harness.start(source, [" aapl ", "googl"])

        assert set(source.get_tickers()) == {"AAPL", "GOOGL"}
        assert all(t == t.strip().upper() for t in harness.cache.get_all())

        await source.stop()

    async def test_start_deduplicates_after_normalizing(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL", "aapl", " AAPL "])

        assert source.get_tickers() == ["AAPL"]

        await source.stop()

    async def test_add_ticker_extends_the_active_set(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        await source.add_ticker("TSLA")

        assert "TSLA" in source.get_tickers()

        await source.stop()

    async def test_add_ticker_normalizes(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        await source.add_ticker("  tsla  ")

        assert "TSLA" in source.get_tickers()
        assert " tsla " not in source.get_tickers()

        await source.stop()

    async def test_add_ticker_is_idempotent(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        await source.add_ticker("AAPL")
        await source.add_ticker("aapl")

        assert source.get_tickers().count("AAPL") == 1

        await source.stop()

    async def test_remove_ticker_evicts_from_the_cache(self, harness):
        """Obligation 3: a removed ticker must not linger in every SSE frame."""
        source = harness.make()
        await harness.start(source, ["AAPL", "GOOGL"])
        assert harness.cache.get("GOOGL") is not None

        await source.remove_ticker("GOOGL")

        assert "GOOGL" not in source.get_tickers()
        assert harness.cache.get("GOOGL") is None

        await source.stop()

    async def test_remove_ticker_normalizes(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL", "GOOGL"])

        await source.remove_ticker(" googl ")

        assert "GOOGL" not in source.get_tickers()
        assert harness.cache.get("GOOGL") is None

        await source.stop()

    async def test_remove_unknown_ticker_is_a_noop(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        await source.remove_ticker("NOPE")

        assert source.get_tickers() == ["AAPL"]

        await source.stop()

    async def test_get_tickers_returns_a_copy(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        source.get_tickers().append("MUTATED")

        assert source.get_tickers() == ["AAPL"]

        await source.stop()

    async def test_stop_is_safe_before_start(self, harness):
        """Obligation 6."""
        await harness.make().stop()

    async def test_stop_is_idempotent(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        await source.stop()
        await source.stop()

    async def test_start_with_no_tickers_is_harmless(self, harness):
        source = harness.make()
        await harness.start(source, [])

        assert source.get_tickers() == []
        assert len(harness.cache) == 0

        await source.stop()

    async def test_cache_entries_are_well_formed(self, harness):
        source = harness.make()
        await harness.start(source, ["AAPL"])

        update = harness.cache.get("AAPL")
        assert update.ticker == "AAPL"
        assert update.price > 0
        assert update.direction in {"up", "down", "flat"}
        # Unix SECONDS, always — not ms, us or ns.
        assert 1_000_000_000 < update.timestamp < 3_000_000_000

        await source.stop()
