"""The action executor: trades through the shared service, failures as error actions."""

from __future__ import annotations

import pytest

from app.llm.executor import execute_actions
from app.llm.models import ChatResponse, ChatTrade, ChatWatchlistChange

from .fakes import TradeError


class TestTradeExecution:
    async def test_a_buy_goes_through_the_shared_trade_service(self, price_cache, trading, source):
        response = ChatResponse(
            message="ok", trades=[ChatTrade(ticker="AAPL", side="buy", quantity=5)]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)

        assert trading.calls == [
            {
                "ticker": "AAPL",
                "side": "buy",
                "quantity": 5.0,
                "price_cache": price_cache,
                "user_id": "default",
            }
        ]
        assert len(actions) == 1
        action = actions[0]
        assert action.type == "trade"
        assert action.status == "ok"
        assert action.detail == "Bought 5 AAPL @ $192.00"
        assert action.data == {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 5.0,
            "price": 192.0,
            "total": 960.0,
        }

    async def test_a_sell_reads_as_sold(self, price_cache, source):
        response = ChatResponse(
            message="ok", trades=[ChatTrade(ticker="GOOGL", side="sell", quantity=2)]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].detail == "Sold 2 GOOGL @ $175.00"

    async def test_tickers_are_normalised_before_execution(self, price_cache, trading, source):
        response = ChatResponse(
            message="ok", trades=[ChatTrade(ticker="  aapl ", side="buy", quantity=1)]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert trading.calls[0]["ticker"] == "AAPL"
        assert actions[0].data["ticker"] == "AAPL"

    async def test_a_rejected_trade_becomes_an_error_action_not_an_exception(
        self, price_cache, trading, source
    ):
        trading.error = TradeError(
            "INSUFFICIENT_CASH", "Insufficient cash: need $50,000.00, have $9,393.00"
        )
        response = ChatResponse(
            message="ok", trades=[ChatTrade(ticker="TSLA", side="buy", quantity=200)]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)

        assert actions[0].status == "error"
        assert actions[0].detail == "Insufficient cash: need $50,000.00, have $9,393.00"
        assert actions[0].data == {"ticker": "TSLA", "side": "buy", "quantity": 200.0}

    async def test_an_unexpected_trade_failure_is_still_only_an_error_action(
        self, price_cache, trading, source
    ):
        import sys

        def boom(*args, **kwargs):
            raise RuntimeError("database on fire")

        sys.modules["app.services.trading"].execute_trade = boom
        response = ChatResponse(
            message="ok", trades=[ChatTrade(ticker="AAPL", side="buy", quantity=1)]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "error"
        assert "database on fire" in actions[0].detail

    @pytest.mark.parametrize("ticker", ["", "   ", "TOOLONGTICKER", "AA PL", "123"])
    async def test_a_malformed_ticker_is_rejected_before_the_trade_service(
        self, ticker, price_cache, trading, source
    ):
        response = ChatResponse(
            message="ok", trades=[ChatTrade(ticker=ticker, side="buy", quantity=1)]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "error"
        assert "not a valid ticker" in actions[0].detail
        assert trading.calls == []

    async def test_trades_execute_in_the_order_the_model_listed_them(
        self, price_cache, trading, source
    ):
        response = ChatResponse(
            message="ok",
            trades=[
                ChatTrade(ticker="AAPL", side="buy", quantity=1),
                ChatTrade(ticker="GOOGL", side="buy", quantity=2),
            ],
        )
        await execute_actions(response, price_cache=price_cache, market_source=source)
        assert [call["ticker"] for call in trading.calls] == ["AAPL", "GOOGL"]


class TestWatchlistExecution:
    async def test_add_writes_to_the_db_and_registers_with_the_source(
        self, price_cache, db, source
    ):
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="pypl", action="add")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)

        assert [row["ticker"] for row in db.watchlist] == ["AAPL", "GOOGL", "PYPL"]
        assert source.added == ["PYPL"]
        assert actions[0].status == "ok"
        assert actions[0].detail == "Added PYPL to the watchlist"
        assert actions[0].data == {"ticker": "PYPL", "action": "add"}

    async def test_adding_a_duplicate_is_an_error_action(self, price_cache, db, source):
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="AAPL", action="add")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "error"
        assert actions[0].detail == "AAPL is already on the watchlist."
        assert source.added == []

    async def test_a_full_watchlist_is_an_error_action(self, price_cache, db, source):
        db.watchlist_cap = 2
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "error"
        assert "full" in actions[0].detail

    async def test_remove_unregisters_when_no_position_holds_the_ticker(
        self, price_cache, db, source
    ):
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="GOOGL", action="remove")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)

        assert [row["ticker"] for row in db.watchlist] == ["AAPL"]
        assert source.removed == ["GOOGL"]
        assert actions[0].detail == "Removed GOOGL from the watchlist"

    async def test_remove_keeps_streaming_a_ticker_that_is_still_held(
        self, price_cache, db, source
    ):
        """A held position must stay priced or the portfolio cannot be valued."""
        db.positions = [{"id": "p1", "ticker": "AAPL", "quantity": 3.0, "avg_cost": 190.0}]
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="AAPL", action="remove")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)

        assert [row["ticker"] for row in db.watchlist] == ["GOOGL"]
        assert source.removed == []
        assert actions[0].status == "ok"

    async def test_removing_an_unwatched_ticker_is_an_error_action(self, price_cache, db, source):
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="remove")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "error"
        assert actions[0].detail == "PYPL is not on the watchlist."

    async def test_a_malformed_ticker_never_reaches_the_db(self, price_cache, db, source):
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="!!", action="add")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "error"
        assert len(db.watchlist) == 2

    async def test_a_failing_data_source_does_not_undo_a_committed_add(
        self, price_cache, db, source
    ):
        """The row is already committed; the action stays green and the failure is logged."""
        source.fail_on_add = True
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert actions[0].status == "ok"
        assert [row["ticker"] for row in db.watchlist] == ["AAPL", "GOOGL", "PYPL"]

    async def test_a_missing_data_source_still_applies_the_db_change(self, price_cache, db):
        response = ChatResponse(
            message="ok", watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")]
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=None)
        assert actions[0].status == "ok"
        assert [row["ticker"] for row in db.watchlist] == ["AAPL", "GOOGL", "PYPL"]


class TestOrdering:
    async def test_trades_run_before_watchlist_changes(self, price_cache, trading, db, source):
        response = ChatResponse(
            message="ok",
            trades=[ChatTrade(ticker="AAPL", side="buy", quantity=1)],
            watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")],
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert [action.type for action in actions] == ["trade", "watchlist"]

    async def test_no_actions_means_an_empty_list(self, price_cache, source):
        actions = await execute_actions(
            ChatResponse(message="Just chatting."),
            price_cache=price_cache,
            market_source=source,
        )
        assert actions == []

    async def test_one_failure_does_not_stop_the_rest(self, price_cache, db, source):
        response = ChatResponse(
            message="ok",
            watchlist_changes=[
                ChatWatchlistChange(ticker="AAPL", action="add"),  # duplicate -> error
                ChatWatchlistChange(ticker="PYPL", action="add"),  # still applied
            ],
        )
        actions = await execute_actions(response, price_cache=price_cache, market_source=source)
        assert [action.status for action in actions] == ["error", "ok"]
        assert "PYPL" in [row["ticker"] for row in db.watchlist]
