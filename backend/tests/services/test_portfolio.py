"""Portfolio valuation maths (API_CONTRACT §3)."""

from __future__ import annotations

import pytest

from app.market import PriceCache
from app.services.portfolio import STARTING_CASH, build_portfolio, total_portfolio_value


def position(ticker: str, quantity: float, avg_cost: float) -> dict:
    return {
        "id": f"pos-{ticker}",
        "ticker": ticker,
        "quantity": quantity,
        "avg_cost": avg_cost,
        "updated_at": "2026-09-09T14:30:00.000000Z",
    }


def test_empty_portfolio_is_all_cash(db, price_cache):
    portfolio = build_portfolio(price_cache)

    assert portfolio == {
        "cash_balance": 10000.0,
        "positions": [],
        "positions_value": 0,
        "total_value": 10000.0,
        "total_unrealized_pnl": 0,
        "total_return_percent": 0.0,
        "starting_cash": STARTING_CASH,
    }


def test_single_position_fields(db, price_cache):
    db.cash = 8097.5
    db.positions["AAPL"] = position("AAPL", 10.0, 190.25)
    price_cache.update("AAPL", 192.1)

    portfolio = build_portfolio(price_cache)
    holding = portfolio["positions"][0]

    assert holding["cost_basis"] == pytest.approx(1902.5)
    assert holding["market_value"] == pytest.approx(1921.0)
    assert holding["unrealized_pnl"] == pytest.approx(18.5)
    assert holding["unrealized_pnl_percent"] == pytest.approx(0.9724, abs=1e-4)
    # market_value / total_value * 100 exactly; the contract sample rounds it.
    assert holding["weight"] == pytest.approx(1921.0 / 10018.5 * 100)
    assert portfolio["positions_value"] == pytest.approx(1921.0)
    assert portfolio["total_value"] == pytest.approx(10018.5)
    assert portfolio["total_unrealized_pnl"] == pytest.approx(18.5)
    assert portfolio["total_return_percent"] == pytest.approx(0.185)


def test_position_without_a_cached_price_reports_avg_cost(db):
    """P&L must read zero, not nonsense, and current_price is never null."""
    empty_cache = PriceCache()
    db.positions["ZZZZ"] = position("ZZZZ", 4.0, 25.0)

    holding = build_portfolio(empty_cache)["positions"][0]

    assert holding["current_price"] == 25.0
    assert holding["unrealized_pnl"] == 0.0
    assert holding["unrealized_pnl_percent"] == 0.0


def test_positions_are_sorted_by_market_value_descending(db, price_cache):
    db.positions["AAPL"] = position("AAPL", 1.0, 190.0)
    db.positions["GOOGL"] = position("GOOGL", 10.0, 175.0)

    tickers = [row["ticker"] for row in build_portfolio(price_cache)["positions"]]

    assert tickers == ["GOOGL", "AAPL"]


def test_weights_sum_to_the_invested_share_of_the_portfolio(db, price_cache):
    db.cash = 0.0
    db.positions["AAPL"] = position("AAPL", 10.0, 100.0)
    db.positions["GOOGL"] = position("GOOGL", 10.0, 100.0)

    portfolio = build_portfolio(price_cache)

    assert sum(row["weight"] for row in portfolio["positions"]) == pytest.approx(100.0)


def test_dust_quantities_are_not_reported(db, price_cache):
    """A full sell can leave float residue; that is not a holding."""
    db.positions["AAPL"] = position("AAPL", 1e-12, 190.0)

    assert build_portfolio(price_cache)["positions"] == []


def test_zero_cost_basis_does_not_divide_by_zero(db, price_cache):
    db.positions["AAPL"] = position("AAPL", 5.0, 0.0)

    holding = build_portfolio(price_cache)["positions"][0]

    assert holding["unrealized_pnl_percent"] == 0.0
    assert holding["unrealized_pnl"] == pytest.approx(950.0)


def test_weight_is_zero_when_the_portfolio_is_worthless(db):
    db.cash = 0.0
    db.positions["AAPL"] = position("AAPL", 5.0, 0.0)
    cache = PriceCache()
    cache.update("AAPL", 0.0)

    portfolio = build_portfolio(cache)

    assert portfolio["total_value"] == 0.0
    assert portfolio["positions"][0]["weight"] == 0.0


def test_money_is_not_pre_rounded(db, price_cache):
    """Rounding here makes the position P&Ls stop summing to the total."""
    db.positions["AAPL"] = position("AAPL", 1 / 3, 190.0)

    holding = build_portfolio(price_cache)["positions"][0]

    assert holding["market_value"] == pytest.approx(190.0 / 3)
    assert holding["market_value"] != round(holding["market_value"], 2)


def test_total_return_percent_tracks_losses(db, price_cache):
    db.cash = 9000.0

    assert build_portfolio(price_cache)["total_return_percent"] == pytest.approx(-10.0)


def test_total_portfolio_value_helper(db, price_cache):
    db.cash = 500.0
    db.positions["AAPL"] = position("AAPL", 2.0, 100.0)

    assert total_portfolio_value(price_cache) == pytest.approx(500.0 + 380.0)
