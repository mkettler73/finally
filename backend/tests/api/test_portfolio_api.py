"""GET /api/portfolio, POST /api/portfolio/trade, GET /api/portfolio/history."""

from __future__ import annotations

import pytest


def test_get_portfolio_shape(client):
    response = client.get("/api/portfolio")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "cash_balance",
        "positions",
        "positions_value",
        "total_value",
        "total_unrealized_pnl",
        "total_return_percent",
        "starting_cash",
    }
    assert body["cash_balance"] == 10000.0
    assert body["positions"] == []
    assert body["starting_cash"] == 10000.0


def test_get_portfolio_values_positions_live(harness, client):
    harness.client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )
    harness.price_cache.update("AAPL", 200.0)

    holding = client.get("/api/portfolio").json()["positions"][0]

    assert holding["ticker"] == "AAPL"
    assert holding["current_price"] == 200.0
    assert holding["unrealized_pnl"] == pytest.approx(100.0)


def test_buy_returns_trade_and_portfolio(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "aapl", "quantity": 10, "side": "buy"}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"trade", "portfolio"}
    assert body["trade"]["ticker"] == "AAPL"
    assert body["trade"]["total"] == pytest.approx(1900.0)
    assert body["portfolio"]["cash_balance"] == pytest.approx(8100.0)
    assert body["portfolio"]["positions"][0]["quantity"] == 10.0


def test_sell_updates_cash_and_position(harness, client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"})

    body = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 4, "side": "sell"}
    ).json()

    assert body["trade"]["side"] == "sell"
    assert body["portfolio"]["cash_balance"] == pytest.approx(8860.0)
    assert body["portfolio"]["positions"][0]["quantity"] == pytest.approx(6.0)


def test_selling_everything_removes_the_position(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 5, "side": "buy"})

    body = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 5, "side": "sell"}
    ).json()

    assert body["portfolio"]["positions"] == []


def test_buying_an_unwatched_ticker_does_not_watch_it(harness, client):
    harness.price_cache.update("PYPL", 60.0)

    client.post("/api/portfolio/trade", json={"ticker": "PYPL", "quantity": 1, "side": "buy"})

    watched = [row["ticker"] for row in client.get("/api/watchlist").json()["tickers"]]
    assert "PYPL" not in watched
    assert "PYPL" in harness.market_source.get_tickers()


def test_insufficient_cash_envelope(harness, client):
    harness.db.cash = 100.0

    response = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CASH"
    assert "$100.00" in response.json()["detail"]["message"]


def test_insufficient_shares_envelope(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "sell"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INSUFFICIENT_SHARES"


def test_price_unavailable_envelope(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "ZZZZ", "quantity": 1, "side": "buy"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PRICE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"ticker": "AAPL", "quantity": 0, "side": "buy"}, "INVALID_QUANTITY"),
        ({"ticker": "AAPL", "quantity": -5, "side": "buy"}, "INVALID_QUANTITY"),
        ({"ticker": "AAPL", "quantity": 1, "side": "hold"}, "INVALID_SIDE"),
        ({"ticker": "", "quantity": 1, "side": "buy"}, "INVALID_TICKER"),
        ({"ticker": "AA PL", "quantity": 1, "side": "buy"}, "INVALID_TICKER"),
    ],
)
def test_trade_validation_codes(client, payload, code):
    response = client.post("/api/portfolio/trade", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code


def test_a_rejected_trade_changes_nothing(harness, client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": -1, "side": "buy"})

    assert harness.db.cash == 10000.0
    assert harness.db.trades == []


def test_history_is_oldest_first(harness, client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})

    snapshots = client.get("/api/portfolio/history").json()["snapshots"]

    assert len(snapshots) >= 2
    assert [row["recorded_at"] for row in snapshots] == sorted(
        row["recorded_at"] for row in snapshots
    )
    assert set(snapshots[0]) == {"total_value", "recorded_at"}


def test_history_is_never_empty(client):
    """The seeded snapshot keeps the P&L chart from rendering nothing."""
    assert client.get("/api/portfolio/history").json()["snapshots"]


def test_history_limit_returns_the_most_recent(harness, client):
    for value in range(5):
        harness.db.record_snapshot(float(value))

    snapshots = client.get("/api/portfolio/history?limit=2").json()["snapshots"]

    assert [row["total_value"] for row in snapshots] == [3.0, 4.0]


@pytest.mark.parametrize("limit", [0, -1, 5001, "many"])
def test_history_rejects_bad_limits(client, limit):
    response = client.get(f"/api/portfolio/history?limit={limit}")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
