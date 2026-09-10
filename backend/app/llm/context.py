"""Builds the portfolio / watchlist / history context handed to the LLM.

The numbers here follow the same definitions as `GET /api/portfolio`
(API_CONTRACT.md section 3) so the model never sees a figure that contradicts
the UI. This module deliberately reads the database and the price cache
directly and does *not* call the HTTP API - a chat turn should not depend on
the server being able to reach itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..market import PriceCache
from ._deps import get_db
from ._rows import field as row_field

STARTING_CASH = 10_000.0

# How much of the value curve to show the model. Enough to describe a trend,
# small enough that it does not crowd out the positions.
HISTORY_POINTS = 12


@dataclass(frozen=True, slots=True)
class PositionContext:
    ticker: str
    quantity: float
    avg_cost: float
    current_price: float
    market_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_percent: float
    weight: float


@dataclass(frozen=True, slots=True)
class WatchlistContext:
    ticker: str
    price: float | None
    change_percent: float | None
    direction: str


@dataclass(frozen=True, slots=True)
class SnapshotContext:
    total_value: float
    recorded_at: str


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    """Everything the model is told about the account, in one immutable value."""

    cash_balance: float
    positions: list[PositionContext] = field(default_factory=list)
    watchlist: list[WatchlistContext] = field(default_factory=list)
    history: list[SnapshotContext] = field(default_factory=list)
    positions_value: float = 0.0
    total_value: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_return_percent: float = 0.0
    starting_cash: float = STARTING_CASH

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def known_tickers(self) -> list[str]:
        """Tickers the user has some relationship with, positions first.

        Used by mock mode to resolve a bare word in a message to a real ticker.
        """
        seen: dict[str, None] = {}
        for position in self.positions:
            seen.setdefault(position.ticker, None)
        for entry in self.watchlist:
            seen.setdefault(entry.ticker, None)
        return list(seen)


def build_context(price_cache: PriceCache, user_id: str = "default") -> PortfolioContext:
    """Assemble the full chat context from the database and the price cache."""
    db = get_db()

    cash_balance = float(db.get_cash_balance(user_id))
    positions = _build_positions(db.list_positions(user_id), price_cache)

    positions_value = sum(position.market_value for position in positions)
    total_value = cash_balance + positions_value
    total_unrealized_pnl = sum(position.unrealized_pnl for position in positions)

    # weight needs the total, so it is filled in on a second pass.
    positions = [
        _with_weight(position, total_value)
        for position in sorted(positions, key=lambda p: p.market_value, reverse=True)
    ]

    return PortfolioContext(
        cash_balance=cash_balance,
        positions=positions,
        watchlist=_build_watchlist(db.list_watchlist(user_id), price_cache),
        history=[
            SnapshotContext(
                total_value=float(row_field(row, "total_value", 0.0)),
                recorded_at=str(row_field(row, "recorded_at", "")),
            )
            for row in db.list_snapshots(HISTORY_POINTS, user_id)
        ],
        positions_value=positions_value,
        total_value=total_value,
        total_unrealized_pnl=total_unrealized_pnl,
        total_return_percent=(total_value - STARTING_CASH) / STARTING_CASH * 100,
        starting_cash=STARTING_CASH,
    )


def _build_positions(rows: list, price_cache: PriceCache) -> list[PositionContext]:
    positions: list[PositionContext] = []
    for row in rows:
        ticker = str(row_field(row, "ticker", ""))
        quantity = float(row_field(row, "quantity", 0.0))
        avg_cost = float(row_field(row, "avg_cost", 0.0))
        # An unpriced position values at cost, so P&L reads 0 rather than
        # nonsense (API_CONTRACT section 3).
        current_price = price_cache.get_price(ticker)
        if current_price is None:
            current_price = avg_cost

        cost_basis = quantity * avg_cost
        market_value = quantity * current_price
        unrealized_pnl = market_value - cost_basis
        positions.append(
            PositionContext(
                ticker=ticker,
                quantity=quantity,
                avg_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                cost_basis=cost_basis,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_percent=(unrealized_pnl / cost_basis * 100 if cost_basis else 0.0),
                weight=0.0,
            )
        )
    return positions


def _with_weight(position: PositionContext, total_value: float) -> PositionContext:
    weight = position.market_value / total_value * 100 if total_value else 0.0
    return PositionContext(
        ticker=position.ticker,
        quantity=position.quantity,
        avg_cost=position.avg_cost,
        current_price=position.current_price,
        market_value=position.market_value,
        cost_basis=position.cost_basis,
        unrealized_pnl=position.unrealized_pnl,
        unrealized_pnl_percent=position.unrealized_pnl_percent,
        weight=weight,
    )


def _build_watchlist(rows: list, price_cache: PriceCache) -> list[WatchlistContext]:
    entries: list[WatchlistContext] = []
    for row in rows:
        ticker = str(row_field(row, "ticker", ""))
        update = price_cache.get(ticker)
        entries.append(
            WatchlistContext(
                ticker=ticker,
                price=update.price if update else None,
                change_percent=update.change_percent if update else None,
                direction=update.direction if update else "flat",
            )
        )
    return entries


# --- Rendering ------------------------------------------------------------


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:+.2f}%"


def render_context(context: PortfolioContext) -> str:
    """Render the context as the compact text block embedded in the prompt.

    Plain text rather than JSON: it is markedly cheaper in tokens and the model
    reads a small table at least as reliably as it reads nested objects.
    """
    lines = [
        "=== ACCOUNT ===",
        f"Cash: {_money(context.cash_balance)}",
        f"Positions value: {_money(context.positions_value)}",
        f"Total value: {_money(context.total_value)}",
        f"Unrealized P&L: {_money(context.total_unrealized_pnl)}",
        f"Total return vs {_money(context.starting_cash)} start: "
        f"{_percent(context.total_return_percent)}",
        "",
        "=== POSITIONS ===",
    ]

    if context.positions:
        lines.append("ticker | qty | avg_cost | price | mkt_value | unreal_pnl | pnl% | weight%")
        for position in context.positions:
            lines.append(
                f"{position.ticker} | {position.quantity:g} | {position.avg_cost:.2f} | "
                f"{position.current_price:.2f} | {position.market_value:.2f} | "
                f"{position.unrealized_pnl:+.2f} | {position.unrealized_pnl_percent:+.2f} | "
                f"{position.weight:.2f}"
            )
    else:
        lines.append("(no open positions)")

    lines += ["", "=== WATCHLIST ==="]
    if context.watchlist:
        for entry in context.watchlist:
            if entry.price is None:
                lines.append(f"{entry.ticker} | no price yet")
            else:
                lines.append(
                    f"{entry.ticker} | {entry.price:.2f} | "
                    f"{entry.change_percent:+.2f}% | {entry.direction}"
                )
    else:
        lines.append("(watchlist is empty)")

    lines += ["", "=== PORTFOLIO VALUE HISTORY (oldest first) ==="]
    if context.history:
        lines.append(", ".join(f"{snapshot.total_value:.2f}" for snapshot in context.history))
    else:
        lines.append("(no snapshots yet)")

    return "\n".join(lines)
