"""Mock mode. These strings are a contract with the E2E suite - see planning/LLM_NOTES.md."""

from __future__ import annotations

import pytest

from app.llm.context import PortfolioContext, PositionContext, WatchlistContext
from app.llm.mock import MockChatClient, build_mock_response, format_quantity


def make_context(
    cash: float = 10_000.0,
    positions: int = 0,
    watchlist: tuple[str, ...] = ("AAPL", "GOOGL", "NVDA"),
) -> PortfolioContext:
    return PortfolioContext(
        cash_balance=cash,
        positions=[
            PositionContext(
                ticker=f"P{i}",
                quantity=1.0,
                avg_cost=1.0,
                current_price=1.0,
                market_value=1.0,
                cost_basis=1.0,
                unrealized_pnl=0.0,
                unrealized_pnl_percent=0.0,
                weight=0.0,
            )
            for i in range(positions)
        ],
        watchlist=[
            WatchlistContext(ticker=t, price=100.0, change_percent=0.0, direction="flat")
            for t in watchlist
        ],
    )


class TestBuyAndSell:
    def test_buy_with_a_known_ticker_and_quantity(self):
        response = build_mock_response("Buy 5 shares of NVDA", make_context())
        assert response.message == "Buying 5 NVDA at the market price now."
        assert len(response.trades) == 1
        trade = response.trades[0]
        assert (trade.ticker, trade.side, trade.quantity) == ("NVDA", "buy", 5.0)
        assert response.watchlist_changes == []

    def test_sell(self):
        response = build_mock_response("sell 2 AAPL", make_context())
        assert response.message == "Selling 2 AAPL at the market price now."
        assert response.trades[0].side == "sell"
        assert response.trades[0].quantity == 2.0

    def test_fractional_quantity_is_preserved(self):
        response = build_mock_response("buy 2.5 AAPL", make_context())
        assert response.trades[0].quantity == 2.5
        assert response.message == "Buying 2.5 AAPL at the market price now."

    def test_unknown_uppercase_ticker_is_accepted(self):
        response = build_mock_response("buy 3 PYPL please", make_context())
        assert response.trades[0].ticker == "PYPL"

    def test_lowercase_unknown_ticker_falls_through_the_stopword_filter(self):
        response = build_mock_response("buy 3 pypl", make_context())
        assert response.trades[0].ticker == "PYPL"

    def test_buy_without_a_quantity_is_not_a_trade(self):
        response = build_mock_response("should I buy AAPL?", make_context())
        assert response.trades == []
        assert "position" in response.message

    def test_buy_without_a_resolvable_ticker_is_not_a_trade(self):
        response = build_mock_response("buy 5", make_context())
        assert response.trades == []


class TestWatchlist:
    def test_add(self):
        response = build_mock_response("add PYPL to my watchlist", make_context())
        assert response.message == "Adding PYPL to your watchlist."
        assert len(response.watchlist_changes) == 1
        assert response.watchlist_changes[0].action == "add"
        assert response.watchlist_changes[0].ticker == "PYPL"
        assert response.trades == []

    def test_watch(self):
        response = build_mock_response("watch NVDA for me", make_context())
        assert response.watchlist_changes[0].action == "add"

    def test_remove(self):
        response = build_mock_response("remove GOOGL", make_context())
        assert response.message == "Removing GOOGL from your watchlist."
        assert response.watchlist_changes[0].action == "remove"

    def test_unwatch_does_not_match_the_add_branch(self):
        """`\\bwatch\\b` must not fire inside `unwatch`, or removal inverts to addition."""
        response = build_mock_response("unwatch GOOGL", make_context())
        assert response.watchlist_changes[0].action == "remove"

    def test_the_word_watchlist_alone_does_not_trigger_an_add(self):
        response = build_mock_response("what is on my watchlist?", make_context())
        assert response.watchlist_changes == []

    def test_removal_wins_over_a_stray_add(self):
        response = build_mock_response("remove NVDA and add nothing", make_context())
        assert response.watchlist_changes[0].action == "remove"
        assert response.watchlist_changes[0].ticker == "NVDA"


class TestFallback:
    def test_reports_cash_and_position_count(self):
        response = build_mock_response("how am I doing?", make_context(cash=9393.0, positions=3))
        assert response.message == (
            "You are holding 3 positions with $9,393.00 in cash. "
            "Ask me to buy or sell a ticker, or to add one to your watchlist."
        )
        assert response.trades == []
        assert response.watchlist_changes == []

    def test_singular_position(self):
        response = build_mock_response("analyse my portfolio", make_context(positions=1))
        assert "1 position with" in response.message

    def test_zero_positions_is_plural(self):
        response = build_mock_response("hello", make_context())
        assert response.message.startswith("You are holding 0 positions with $10,000.00 in cash.")


class TestDeterminism:
    @pytest.mark.parametrize(
        "message",
        ["Buy 5 NVDA", "sell 1 AAPL", "add PYPL", "remove AAPL", "what should I do?"],
    )
    def test_same_input_gives_the_same_output(self, message):
        context = make_context()
        first = build_mock_response(message, context)
        second = build_mock_response(message, context)
        assert first.model_dump() == second.model_dump()


class TestMockChatClient:
    def test_is_flagged_as_mock(self):
        assert MockChatClient().is_mock is True

    def test_complete_matches_build_mock_response(self):
        context = make_context()
        client = MockChatClient()
        assert client.complete(
            user_message="Buy 5 NVDA", context=context, history=[{"role": "user", "content": "x"}]
        ) == build_mock_response("Buy 5 NVDA", context)

    def test_carries_no_network_client(self):
        """Mock mode must be structurally incapable of calling out."""
        client = MockChatClient()
        assert not hasattr(client, "_completion_fn")
        assert not hasattr(client, "model")


class TestFormatQuantity:
    @pytest.mark.parametrize(
        ("value", "expected"), [(5.0, "5"), (1.0, "1"), (2.5, "2.5"), (0.25, "0.25")]
    )
    def test_whole_numbers_lose_the_decimal(self, value, expected):
        assert format_quantity(value) == expected
