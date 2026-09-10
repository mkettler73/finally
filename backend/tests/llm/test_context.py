"""The context builder: the numbers the model is shown."""

from __future__ import annotations

import sys

from app.llm.context import build_context, render_context
from app.market import PriceCache


class TestBuildContext:
    def test_empty_account(self, price_cache, db):
        context = build_context(price_cache)
        assert context.cash_balance == 10_000.0
        assert context.positions == []
        assert context.position_count == 0
        assert context.positions_value == 0.0
        assert context.total_value == 10_000.0
        assert context.total_return_percent == 0.0

    def test_position_pnl_matches_the_portfolio_contract(self, price_cache, db):
        db.positions = [
            {"id": "p1", "ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.25},
        ]
        db.cash_balance = 8097.5

        context = build_context(price_cache)
        position = context.positions[0]

        assert position.current_price == 192.0
        assert position.cost_basis == 1902.5
        assert position.market_value == 1920.0
        assert position.unrealized_pnl == 1920.0 - 1902.5
        assert position.unrealized_pnl_percent == (1920.0 - 1902.5) / 1902.5 * 100
        assert context.total_value == 8097.5 + 1920.0
        assert position.weight == 1920.0 / context.total_value * 100

    def test_unpriced_position_values_at_cost(self, db):
        db.positions = [{"id": "p1", "ticker": "ZZZZ", "quantity": 4.0, "avg_cost": 50.0}]
        context = build_context(PriceCache())
        position = context.positions[0]
        assert position.current_price == 50.0
        assert position.unrealized_pnl == 0.0
        assert position.unrealized_pnl_percent == 0.0

    def test_zero_cost_basis_does_not_divide_by_zero(self, db):
        db.positions = [{"id": "p1", "ticker": "AAPL", "quantity": 0.0, "avg_cost": 0.0}]
        context = build_context(PriceCache())
        assert context.positions[0].unrealized_pnl_percent == 0.0
        assert context.positions[0].weight == 0.0

    def test_positions_sorted_by_market_value_descending(self, price_cache, db):
        db.positions = [
            {"id": "p1", "ticker": "GOOGL", "quantity": 1.0, "avg_cost": 175.0},
            {"id": "p2", "ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.0},
        ]
        context = build_context(price_cache)
        assert [p.ticker for p in context.positions] == ["AAPL", "GOOGL"]

    def test_total_return_percent_is_relative_to_starting_cash(self, price_cache, db):
        db.cash_balance = 10_500.0
        context = build_context(price_cache)
        assert context.total_return_percent == 5.0

    def test_watchlist_carries_live_prices(self, price_cache, db):
        context = build_context(price_cache)
        assert [entry.ticker for entry in context.watchlist] == ["AAPL", "GOOGL"]
        assert context.watchlist[0].price == 192.0
        assert context.watchlist[0].direction == "up"

    def test_watchlist_entry_without_a_price_is_null_and_flat(self, db):
        context = build_context(PriceCache())
        assert context.watchlist[0].price is None
        assert context.watchlist[0].change_percent is None
        assert context.watchlist[0].direction == "flat"

    def test_history_is_limited_and_ordered(self, price_cache, db):
        db.snapshots = [
            {"total_value": float(i), "recorded_at": f"2026-09-09T14:{i:02d}:00.000000Z"}
            for i in range(30)
        ]
        context = build_context(price_cache)
        assert len(context.history) == 12
        assert context.history[0].total_value == 18.0
        assert context.history[-1].total_value == 29.0

    def test_user_id_is_threaded_through(self, price_cache, db):
        """Every repository call must carry the caller's user_id, not the default."""
        seen: list[str] = []
        module = sys.modules["app.db"]
        module.get_cash_balance = lambda user_id="default": (seen.append(user_id), 10_000.0)[1]
        module.list_positions = lambda user_id="default": (seen.append(user_id), [])[1]
        module.list_watchlist = lambda user_id="default": (seen.append(user_id), [])[1]
        module.list_snapshots = lambda limit=500, user_id="default": (seen.append(user_id), [])[1]

        build_context(price_cache, "someone-else")

        assert seen == ["someone-else"] * 4

    def test_known_tickers_lists_positions_before_watchlist(self, price_cache, db):
        db.positions = [{"id": "p1", "ticker": "NVDA", "quantity": 1.0, "avg_cost": 100.0}]
        context = build_context(price_cache)
        assert context.known_tickers == ["NVDA", "AAPL", "GOOGL"]


class TestRenderContext:
    def test_renders_every_section(self, price_cache, db):
        db.positions = [{"id": "p1", "ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.25}]
        text = render_context(build_context(price_cache))

        assert "=== ACCOUNT ===" in text
        assert "=== POSITIONS ===" in text
        assert "=== WATCHLIST ===" in text
        assert "=== PORTFOLIO VALUE HISTORY (oldest first) ===" in text
        assert "AAPL" in text
        assert "$10,000.00" in text

    def test_empty_account_says_so_rather_than_rendering_a_blank(self, db):
        db.watchlist = []
        db.snapshots = []
        text = render_context(build_context(PriceCache()))
        assert "(no open positions)" in text
        assert "(watchlist is empty)" in text
        assert "(no snapshots yet)" in text
