"""The HTTP layer against the real `app.db`, not the in-memory fake.

Everything else in tests/api/ substitutes a fake repository so the API layer is
tested in isolation. This module exists to catch the class of bug that isolation
hides: a signature, row shape or exception type where the two sides of
`planning/DATA_LAYER.md` disagree.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import portfolio_router, register_exception_handlers, watchlist_router
from app.db import reset_db_for_tests
from app.db.connection import close_connection
from app.market import PriceCache
from app.services.market_registry import clear_market_source, set_market_source
from app.services.session_open import SessionOpenTracker

from ..services.fake_market import FakeMarketSource


@pytest.fixture
def real_db_client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "finally.db"))
    reset_db_for_tests()

    price_cache = PriceCache()
    price_cache.update("AAPL", 190.0)
    price_cache.update("GOOGL", 175.0)
    session_opens = SessionOpenTracker()
    session_opens.observe_cache(price_cache)
    set_market_source(FakeMarketSource(price_cache, tickers=["AAPL", "GOOGL"]), None)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(portfolio_router)
    app.include_router(watchlist_router)
    app.state.price_cache = price_cache
    app.state.session_opens = session_opens

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    clear_market_source()
    close_connection()


def test_seeded_state_is_visible_through_the_api(real_db_client):
    portfolio = real_db_client.get("/api/portfolio").json()
    watchlist = real_db_client.get("/api/watchlist").json()

    assert portfolio["cash_balance"] == 10000.0
    assert portfolio["positions"] == []
    assert [row["ticker"] for row in watchlist["tickers"]][:3] == ["AAPL", "GOOGL", "MSFT"]
    assert real_db_client.get("/api/portfolio/history").json()["snapshots"]


def test_buy_then_sell_round_trip(real_db_client):
    buy = real_db_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )
    assert buy.status_code == 200, buy.text
    assert buy.json()["portfolio"]["cash_balance"] == pytest.approx(8100.0)

    sell = real_db_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "sell"}
    )
    assert sell.status_code == 200, sell.text
    assert sell.json()["portfolio"]["positions"] == []
    assert sell.json()["portfolio"]["cash_balance"] == pytest.approx(10000.0)


def test_weighted_average_cost_survives_the_real_repository(real_db_client):
    real_db_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )
    real_db_client.app.state.price_cache.update("AAPL", 210.0)
    body = real_db_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    ).json()

    assert body["portfolio"]["positions"][0]["avg_cost"] == pytest.approx(200.0)


def test_real_db_funding_failure_maps_to_the_contract_code(real_db_client):
    response = real_db_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1000, "side": "buy"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CASH"


def test_real_db_watchlist_errors_map_to_the_contract_codes(real_db_client):
    assert (
        real_db_client.post("/api/watchlist", json={"ticker": "AAPL"}).json()["detail"]["code"]
        == "TICKER_ALREADY_WATCHED"
    )
    assert real_db_client.delete("/api/watchlist/PYPL").json()["detail"]["code"] == (
        "TICKER_NOT_WATCHED"
    )


def test_watchlist_add_and_remove_round_trip(real_db_client):
    added = real_db_client.post("/api/watchlist", json={"ticker": "pypl"})
    assert added.status_code == 201
    assert added.json()["ticker"]["ticker"] == "PYPL"

    assert real_db_client.delete("/api/watchlist/PYPL").json() == {
        "ticker": "PYPL",
        "removed": True,
    }
    tickers = [row["ticker"] for row in real_db_client.get("/api/watchlist").json()["tickers"]]
    assert "PYPL" not in tickers


def test_every_trade_appends_a_snapshot(real_db_client):
    before = len(real_db_client.get("/api/portfolio/history").json()["snapshots"])

    real_db_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"}
    )

    after = real_db_client.get("/api/portfolio/history").json()["snapshots"]
    assert len(after) == before + 1
    assert after[-1]["total_value"] == pytest.approx(10000.0)
