"""Portfolio valuation — the single source of truth for the P&L maths.

Every field here is defined in API_CONTRACT §3. Money is returned raw and
unrounded: the frontend formats to two decimals, and pre-rounding here makes
the position P&Ls stop summing to the total.
"""

from __future__ import annotations

from typing import Any

from ..market import PriceCache
from .rows import row_to_dict

# Seed cash from PLAN.md §7. Used as the denominator of total_return_percent,
# so it is a constant rather than a reading of the current balance.
STARTING_CASH = 10000.0

# Quantities at or below this are float residue from a full sell, not a holding.
DUST_QUANTITY = 1e-9


def build_position(position: Any, price_cache: PriceCache) -> dict[str, Any]:
    """Value one position row against the price cache (weight filled in later)."""
    row = row_to_dict(position)
    ticker = row["ticker"]
    quantity = float(row["quantity"])
    avg_cost = float(row["avg_cost"])

    # A ticker with no cached price reports its own cost, so P&L reads 0 rather
    # than nonsense, and never null (API_CONTRACT §3).
    cached = price_cache.get_price(ticker)
    current_price = avg_cost if cached is None else cached

    cost_basis = quantity * avg_cost
    market_value = quantity * current_price
    unrealized_pnl = market_value - cost_basis

    return {
        "ticker": ticker,
        "quantity": quantity,
        "avg_cost": avg_cost,
        "current_price": current_price,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_percent": (unrealized_pnl / cost_basis * 100 if cost_basis != 0 else 0.0),
        "weight": 0.0,
    }


def build_portfolio(price_cache: PriceCache, user_id: str = "default") -> dict[str, Any]:
    """The full `GET /api/portfolio` payload."""
    from .. import db

    # One snapshot for both reads. A trade committing between them would give
    # cash-after with positions-before, wrong by the whole trade notional --
    # and the 30s snapshot task persists whatever it reads, turning that race
    # into a permanent artefact in the P&L chart.
    with db.read_transaction():
        cash_balance = float(db.get_cash_balance(user_id))
        position_rows = list(db.list_positions(user_id))

    positions = [
        build_position(row, price_cache)
        for row in position_rows
        if abs(float(row_to_dict(row)["quantity"])) > DUST_QUANTITY
    ]

    positions_value = sum(position["market_value"] for position in positions)
    total_value = cash_balance + positions_value
    total_unrealized_pnl = sum(position["unrealized_pnl"] for position in positions)

    if total_value != 0:
        for position in positions:
            position["weight"] = position["market_value"] / total_value * 100

    positions.sort(key=lambda position: position["market_value"], reverse=True)

    return {
        "cash_balance": cash_balance,
        "positions": positions,
        "positions_value": positions_value,
        "total_value": total_value,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_return_percent": (total_value - STARTING_CASH) / STARTING_CASH * 100,
        "starting_cash": STARTING_CASH,
    }


def total_portfolio_value(price_cache: PriceCache, user_id: str = "default") -> float:
    """Cash plus the market value of every position."""
    return float(build_portfolio(price_cache, user_id)["total_value"])
