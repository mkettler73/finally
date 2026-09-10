"""Fixtures for the HTTP layer tests.

The app under test is assembled here rather than imported from ``app.main`` so
no test starts the real market data simulator or the snapshot task; the wiring
in ``main`` has its own test module.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import portfolio_router, register_exception_handlers, watchlist_router
from app.market import PriceCache
from app.services.market_registry import clear_market_source, set_market_source
from app.services.session_open import SessionOpenTracker

from ..services.fake_db import FakeDb, install_fake_db
from ..services.fake_market import FakeMarketSource

SEEDED_PRICES = {"AAPL": 190.0, "GOOGL": 175.0, "MSFT": 410.0}


@dataclass
class Harness:
    """Everything a test needs to drive and inspect one app instance."""

    client: TestClient
    db: FakeDb
    price_cache: PriceCache
    session_opens: SessionOpenTracker
    market_source: FakeMarketSource


@pytest.fixture
def harness(monkeypatch) -> Harness:
    store = install_fake_db(monkeypatch)

    price_cache = PriceCache()
    for ticker, price in SEEDED_PRICES.items():
        price_cache.update(ticker, price)

    session_opens = SessionOpenTracker()
    session_opens.observe_cache(price_cache)

    source = FakeMarketSource(price_cache, tickers=list(SEEDED_PRICES))
    set_market_source(source, None)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(portfolio_router)
    app.include_router(watchlist_router)
    app.state.price_cache = price_cache
    app.state.session_opens = session_opens

    # raise_server_exceptions=False so the catch-all handler's 500 envelope is
    # observable instead of the exception being re-raised into the test.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Harness(client, store, price_cache, session_opens, source)

    clear_market_source()


@pytest.fixture
def client(harness) -> TestClient:
    return harness.client
