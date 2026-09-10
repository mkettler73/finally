"""The shared trade service (API_CONTRACT §7)."""

from __future__ import annotations

import pytest

from app.market import PriceCache
from app.services.trading import TradeError, TradeResult, execute_trade, normalize_ticker


def buy(price_cache, ticker="AAPL", quantity=10.0):
    return execute_trade(ticker, "buy", quantity, price_cache=price_cache)


def test_buy_fills_at_the_cached_price(db, price_cache, market_source):
    result = buy(price_cache, quantity=10.0)

    assert isinstance(result, TradeResult)
    assert result.trade["ticker"] == "AAPL"
    assert result.trade["side"] == "buy"
    assert result.trade["quantity"] == 10.0
    assert result.trade["price"] == 190.0
    assert result.trade["total"] == pytest.approx(1900.0)
    assert result.trade["executed_at"].endswith("Z")
    assert result.trade["id"]


def test_buy_debits_cash_and_creates_the_position(db, price_cache, market_source):
    buy(price_cache, quantity=10.0)

    assert db.cash == pytest.approx(8100.0)
    assert db.positions["AAPL"]["quantity"] == 10.0
    assert db.positions["AAPL"]["avg_cost"] == pytest.approx(190.0)


def test_result_carries_the_post_trade_portfolio(db, price_cache, market_source):
    result = buy(price_cache, quantity=10.0)

    assert result.portfolio["cash_balance"] == pytest.approx(8100.0)
    assert result.portfolio["positions"][0]["ticker"] == "AAPL"
    assert result.to_response() == {"trade": result.trade, "portfolio": result.portfolio}


def test_second_buy_uses_weighted_average_cost(db, price_cache, market_source):
    buy(price_cache, quantity=10.0)
    price_cache.update("AAPL", 210.0)
    buy(price_cache, quantity=10.0)

    assert db.positions["AAPL"]["avg_cost"] == pytest.approx(200.0)


def test_sell_credits_cash_and_leaves_avg_cost_alone(db, price_cache, market_source):
    buy(price_cache, quantity=10.0)
    price_cache.update("AAPL", 200.0)

    execute_trade("AAPL", "sell", 4.0, price_cache=price_cache)

    assert db.positions["AAPL"]["quantity"] == pytest.approx(6.0)
    assert db.positions["AAPL"]["avg_cost"] == pytest.approx(190.0)
    assert db.cash == pytest.approx(8100.0 + 800.0)


def test_full_sell_deletes_the_position(db, price_cache, market_source):
    buy(price_cache, quantity=10.0)

    execute_trade("AAPL", "sell", 10.0, price_cache=price_cache)

    assert "AAPL" not in db.positions


def test_every_trade_writes_a_snapshot(db, price_cache, market_source):
    before = len(db.snapshots)

    buy(price_cache, quantity=1.0)

    assert len(db.snapshots) == before + 1
    assert db.snapshots[-1]["total_value"] == pytest.approx(10000.0)


def test_ticker_is_normalised(db, price_cache, market_source):
    result = execute_trade("  aapl ", "buy", 1.0, price_cache=price_cache)

    assert result.trade["ticker"] == "AAPL"
    assert "AAPL" in db.positions


def test_side_is_case_insensitive(db, price_cache, market_source):
    result = execute_trade("AAPL", "BUY", 1.0, price_cache=price_cache)

    assert result.trade["side"] == "buy"


def test_buying_an_untracked_ticker_starts_streaming_it(db, price_cache, market_source):
    price_cache.update("PYPL", 60.0)

    execute_trade("PYPL", "buy", 1.0, price_cache=price_cache)

    assert market_source.added == ["PYPL"]


def test_buying_a_tracked_ticker_does_not_re_add_it(db, price_cache, market_source):
    buy(price_cache, quantity=1.0)

    assert market_source.added == []


def test_selling_does_not_touch_the_market_source(db, price_cache, market_source):
    buy(price_cache, quantity=2.0)
    execute_trade("AAPL", "sell", 1.0, price_cache=price_cache)

    assert market_source.removed == []


def test_trade_works_without_a_registered_market_source(db, price_cache):
    """The service must be usable outside a running app (e.g. in tests)."""
    result = execute_trade("AAPL", "buy", 1.0, price_cache=price_cache)

    assert result.trade["ticker"] == "AAPL"


def test_insufficient_cash_reports_both_figures(db, price_cache, market_source):
    db.cash = 500.0

    with pytest.raises(TradeError) as excinfo:
        buy(price_cache, quantity=10.0)

    assert excinfo.value.code == "INSUFFICIENT_CASH"
    assert "$1,900.00" in excinfo.value.message
    assert "$500.00" in excinfo.value.message
    assert db.cash == 500.0
    assert db.trades == []


def test_insufficient_shares(db, price_cache, market_source):
    buy(price_cache, quantity=1.0)

    with pytest.raises(TradeError) as excinfo:
        execute_trade("AAPL", "sell", 5.0, price_cache=price_cache)

    assert excinfo.value.code == "INSUFFICIENT_SHARES"
    assert "only 1 held" in excinfo.value.message


def test_selling_a_ticker_never_held(db, price_cache, market_source):
    with pytest.raises(TradeError) as excinfo:
        execute_trade("AAPL", "sell", 1.0, price_cache=price_cache)

    assert excinfo.value.code == "INSUFFICIENT_SHARES"


def test_price_unavailable(db, market_source):
    with pytest.raises(TradeError) as excinfo:
        execute_trade("ZZZZ", "buy", 1.0, price_cache=PriceCache())

    assert excinfo.value.code == "PRICE_UNAVAILABLE"


@pytest.mark.parametrize("quantity", [0, -1, -0.5, float("nan"), float("inf")])
def test_invalid_quantities(db, price_cache, market_source, quantity):
    with pytest.raises(TradeError) as excinfo:
        buy(price_cache, quantity=quantity)

    assert excinfo.value.code == "INVALID_QUANTITY"


def test_non_numeric_quantity(db, price_cache, market_source):
    with pytest.raises(TradeError) as excinfo:
        execute_trade("AAPL", "buy", "ten", price_cache=price_cache)  # type: ignore[arg-type]

    assert excinfo.value.code == "INVALID_QUANTITY"


@pytest.mark.parametrize("side", ["hold", "", "buys", None])
def test_invalid_sides(db, price_cache, market_source, side):
    with pytest.raises(TradeError) as excinfo:
        execute_trade("AAPL", side, 1.0, price_cache=price_cache)  # type: ignore[arg-type]

    assert excinfo.value.code == "INVALID_SIDE"


@pytest.mark.parametrize("ticker", ["", "   ", "TOOLONGTICKER", "AA PL", "123", "AA$PL"])
def test_invalid_tickers(db, price_cache, market_source, ticker):
    with pytest.raises(TradeError) as excinfo:
        execute_trade(ticker, "buy", 1.0, price_cache=price_cache)

    assert excinfo.value.code == "INVALID_TICKER"


@pytest.mark.parametrize("ticker", ["BRK.B", "brk-b", "V"])
def test_valid_ticker_shapes(ticker):
    assert normalize_ticker(ticker) == ticker.strip().upper()


def test_validation_happens_before_the_db_is_touched(db, price_cache, market_source):
    with pytest.raises(TradeError):
        execute_trade("AAPL", "buy", -1, price_cache=price_cache)

    assert db.trades == []
    assert db.cash == 10000.0


def test_db_level_funding_failure_becomes_a_trade_error(
    db, price_cache, market_source, monkeypatch
):
    """The pre-check is advisory; the transaction's check is authoritative, and
    its failure must still surface as a TradeError, not a raw DbError."""
    import app.db as db_module

    def racing_trade(*args, **kwargs):
        raise db_module.InsufficientCashError("Need $1,900.00 but only $0.00 available.")

    monkeypatch.setattr(db_module, "execute_trade_atomic", racing_trade)

    with pytest.raises(TradeError) as excinfo:
        buy(price_cache, quantity=1.0)

    assert excinfo.value.code == "INSUFFICIENT_CASH"


def test_unexpected_db_errors_are_not_swallowed(db, price_cache, market_source, monkeypatch):
    import app.db as db_module

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(db_module, "execute_trade_atomic", boom)

    with pytest.raises(RuntimeError, match="disk on fire"):
        buy(price_cache, quantity=1.0)


def test_fractional_shares(db, price_cache, market_source):
    result = execute_trade("AAPL", "buy", 0.5, price_cache=price_cache)

    assert result.trade["quantity"] == 0.5
    assert result.trade["total"] == pytest.approx(95.0)
