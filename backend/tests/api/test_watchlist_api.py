"""GET/POST/DELETE /api/watchlist."""

from __future__ import annotations

import pytest

from tests.services.fake_db import DEFAULT_WATCHLIST

ROW_FIELDS = {
    "ticker",
    "price",
    "previous_price",
    "change",
    "change_percent",
    "direction",
    "session_open",
    "session_change",
    "session_change_percent",
    "added_at",
}


def test_get_watchlist_returns_the_seeded_order(client):
    response = client.get("/api/watchlist")

    assert response.status_code == 200
    tickers = [row["ticker"] for row in response.json()["tickers"]]
    assert tickers == DEFAULT_WATCHLIST


def test_row_shape_and_live_prices(harness, client):
    harness.price_cache.update("AAPL", 192.1)

    rows = {row["ticker"]: row for row in client.get("/api/watchlist").json()["tickers"]}

    assert set(rows["AAPL"]) == ROW_FIELDS
    assert rows["AAPL"]["price"] == 192.1
    assert rows["AAPL"]["session_open"] == 190.0
    assert rows["AAPL"]["session_change_percent"] == pytest.approx(1.1053, abs=1e-4)


def test_unpriced_ticker_reports_nulls(client):
    """TSLA is seeded in the watchlist but has never ticked in this harness."""
    rows = {row["ticker"]: row for row in client.get("/api/watchlist").json()["tickers"]}

    assert rows["TSLA"]["price"] is None
    assert rows["TSLA"]["direction"] == "flat"
    assert rows["TSLA"]["session_change"] is None


def test_add_ticker_returns_201_and_the_row(harness, client):
    harness.price_cache.update("PYPL", 60.0)

    response = client.post("/api/watchlist", json={"ticker": "pypl"})

    assert response.status_code == 201
    row = response.json()["ticker"]
    assert row["ticker"] == "PYPL"
    assert set(row) == ROW_FIELDS
    assert row["session_open"] == 60.0


def test_add_ticker_starts_streaming_it(harness, client):
    client.post("/api/watchlist", json={"ticker": "PYPL"})

    assert harness.market_source.added == ["PYPL"]
    assert "PYPL" in harness.market_source.get_tickers()


def test_added_ticker_appears_in_the_list(client):
    client.post("/api/watchlist", json={"ticker": "PYPL"})

    tickers = [row["ticker"] for row in client.get("/api/watchlist").json()["tickers"]]
    assert tickers[-1] == "PYPL"


def test_duplicate_ticker_conflicts(client):
    response = client.post("/api/watchlist", json={"ticker": "AAPL"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TICKER_ALREADY_WATCHED"


def test_watchlist_cap(harness, client):
    for index in range(20):
        harness.db.add_to_watchlist(f"FILL{index}")

    response = client.post("/api/watchlist", json={"ticker": "PYPL"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WATCHLIST_FULL"


@pytest.mark.parametrize("ticker", ["", "  ", "AA PL", "TOOLONGTICKER"])
def test_add_rejects_invalid_tickers(client, ticker):
    response = client.post("/api/watchlist", json={"ticker": ticker})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_TICKER"


def test_add_requires_a_ticker_field(client):
    response = client.post("/api/watchlist", json={})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_TICKER"


def test_delete_removes_and_untracks(harness, client):
    response = client.delete("/api/watchlist/AAPL")

    assert response.status_code == 200
    assert response.json() == {"ticker": "AAPL", "removed": True}
    assert harness.market_source.removed == ["AAPL"]
    tickers = [row["ticker"] for row in client.get("/api/watchlist").json()["tickers"]]
    assert "AAPL" not in tickers


def test_delete_is_case_insensitive(client):
    assert client.delete("/api/watchlist/aapl").status_code == 200


def test_delete_keeps_streaming_a_held_ticker(harness, client):
    """A position must stay priced even when it leaves the watchlist."""
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})

    client.delete("/api/watchlist/AAPL")

    assert harness.market_source.removed == []
    assert "AAPL" in harness.market_source.get_tickers()
    assert harness.price_cache.get_price("AAPL") == 190.0


def test_delete_untracks_once_the_position_is_closed(harness, client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "sell"})

    client.delete("/api/watchlist/AAPL")

    assert harness.market_source.removed == ["AAPL"]


def test_delete_unknown_ticker_is_404(harness, client):
    response = client.delete("/api/watchlist/PYPL")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TICKER_NOT_WATCHED"
    assert harness.market_source.removed == []


def test_delete_rejects_an_invalid_ticker(client):
    response = client.delete("/api/watchlist/AA%20PL")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_TICKER"


def test_re_adding_a_removed_ticker_rebaselines_the_session(harness, client):
    client.delete("/api/watchlist/AAPL")
    harness.price_cache.update("AAPL", 250.0)

    row = client.post("/api/watchlist", json={"ticker": "AAPL"}).json()["ticker"]

    assert row["session_open"] == 250.0
