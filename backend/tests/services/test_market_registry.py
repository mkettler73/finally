"""The bridge from synchronous request handlers to the async market source."""

from __future__ import annotations

import asyncio

from app.market import PriceCache
from app.services.market_registry import (
    clear_market_source,
    ensure_tracked,
    get_market_source,
    set_market_source,
    untrack,
)

from .fake_market import FakeMarketSource


def test_no_source_registered_is_a_no_op():
    """Trades must still work outside a running app."""
    ensure_tracked("AAPL")
    untrack("AAPL")

    assert get_market_source() is None


def test_ensure_tracked_adds_an_unknown_ticker():
    source = FakeMarketSource(tickers=["AAPL"])
    set_market_source(source, None)

    ensure_tracked("PYPL")

    assert source.added == ["PYPL"]
    assert "PYPL" in source.get_tickers()


def test_ensure_tracked_skips_a_known_ticker():
    source = FakeMarketSource(tickers=["AAPL"])
    set_market_source(source, None)

    ensure_tracked("AAPL")

    assert source.added == []


def test_untrack_removes_from_source_and_cache():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    source = FakeMarketSource(cache, tickers=["AAPL"])
    set_market_source(source, None)

    untrack("AAPL")

    assert source.removed == ["AAPL"]
    assert "AAPL" not in cache


def test_clear_forgets_the_source():
    set_market_source(FakeMarketSource(), None)

    clear_market_source()

    assert get_market_source() is None


def test_coroutines_are_bridged_onto_the_registered_loop():
    """In the app the caller is a threadpool worker with no loop of its own;
    the coroutine has to be handed to the loop the source was started on."""
    source = FakeMarketSource(tickers=[])
    results: list[str] = []

    async def scenario():
        loop = asyncio.get_running_loop()
        set_market_source(source, loop)
        # A worker thread, exactly like FastAPI's threadpool.
        await asyncio.to_thread(ensure_tracked, "PYPL")
        results.extend(source.get_tickers())

    asyncio.run(scenario())

    assert results == ["PYPL"]
    assert source.added == ["PYPL"]
