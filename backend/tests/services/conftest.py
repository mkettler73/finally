"""Fixtures for the service-layer tests."""

from __future__ import annotations

import pytest

from app.market import PriceCache
from app.services.market_registry import clear_market_source, set_market_source

from .fake_db import FakeDb, install_fake_db
from .fake_market import FakeMarketSource


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    """An in-memory ``app.db`` for the duration of one test."""
    return install_fake_db(monkeypatch)


@pytest.fixture
def price_cache() -> PriceCache:
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("GOOGL", 175.0)
    return cache


@pytest.fixture(autouse=True)
def clean_market_registry():
    """The registry is process-wide state; never leak it between tests."""
    clear_market_source()
    yield
    clear_market_source()


@pytest.fixture
def market_source(price_cache) -> FakeMarketSource:
    source = FakeMarketSource(price_cache, tickers=["AAPL", "GOOGL"])
    set_market_source(source, None)
    return source
