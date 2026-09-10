"""The one and only trade execution path (API_CONTRACT §7).

Both `POST /api/portfolio/trade` and the LLM chat auto-executor call
``execute_trade``. It raises ``TradeError`` rather than ``HTTPException`` so it
is usable outside a request: the router maps the error onto the HTTP envelope,
the chat executor turns it into a failed action and still answers 200.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from ..market import MarketDataSource, PriceCache
from .market_registry import ensure_tracked
from .portfolio import build_portfolio
from .rows import row_to_dict

logger = logging.getLogger(__name__)

# 1-10 characters of A-Z, dot or hyphen, per API_CONTRACT §0.
TICKER_PATTERN = re.compile(r"^[A-Z.\-]{1,10}$")

VALID_SIDES = ("buy", "sell")


class TradeError(Exception):
    """A trade that cannot be executed, with a code from API_CONTRACT §0.

    ``message`` is human-readable and is shown to the user verbatim, both in the
    HTTP error envelope and when the chat layer feeds the failure back to the
    model.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TradeResult:
    """A filled trade plus the portfolio it produced."""

    trade: dict[str, Any]
    portfolio: dict[str, Any]

    def to_response(self) -> dict[str, Any]:
        """The `POST /api/portfolio/trade` 200 body."""
        return {"trade": self.trade, "portfolio": self.portfolio}


def normalize_ticker(ticker: str) -> str:
    """Normalise and validate a user-supplied ticker.

    Raises:
        TradeError: INVALID_TICKER when it is empty or malformed.
    """
    normalized = MarketDataSource.normalize_ticker(ticker or "")
    if not TICKER_PATTERN.match(normalized):
        raise TradeError(
            "INVALID_TICKER",
            f"'{ticker}' is not a valid ticker: expected 1-10 letters.",
        )
    return normalized


def _validate_side(side: str) -> Literal["buy", "sell"]:
    normalized = (side or "").strip().lower()
    if normalized not in VALID_SIDES:
        raise TradeError("INVALID_SIDE", f"Side must be 'buy' or 'sell', got '{side}'.")
    return normalized  # type: ignore[return-value]


def _validate_quantity(quantity: float) -> float:
    try:
        value = float(quantity)
    except (TypeError, ValueError):
        raise TradeError("INVALID_QUANTITY", f"Quantity '{quantity}' is not a number.") from None
    if not math.isfinite(value) or value <= 0:
        raise TradeError("INVALID_QUANTITY", "Quantity must be a positive number.")
    return value


def execute_trade(
    ticker: str,
    side: Literal["buy", "sell"],
    quantity: float,
    *,
    price_cache: PriceCache,
    user_id: str = "default",
) -> TradeResult:
    """Validate, price and persist a market order, then return the new state.

    Instant fill at the cached price — no fees, slippage or partial fills.

    Raises:
        TradeError: on any validation, pricing or funding failure.
    """
    from .. import db

    symbol = normalize_ticker(ticker)
    direction = _validate_side(side)
    shares = _validate_quantity(quantity)

    price = price_cache.get_price(symbol)
    if price is None:
        raise TradeError(
            "PRICE_UNAVAILABLE",
            f"No live price for {symbol} yet — try again in a moment.",
        )

    total = shares * price
    _check_funding(direction, symbol, shares, total, user_id=user_id)

    # A buy moves `total` from cash into the position and a sell moves it back,
    # both valued at the same price, so the portfolio total is unchanged by the
    # fill itself. The snapshot written inside the transaction records that.
    total_value_after = float(build_portfolio(price_cache, user_id)["total_value"])

    try:
        row = db.execute_trade_atomic(
            symbol,
            direction,
            shares,
            price,
            total_value_after,
            user_id,
        )
    except Exception as exc:  # Re-raised below unless it is a known db failure.
        raise _as_trade_error(exc) from exc

    if direction == "buy":
        # Keep a newly bought ticker streaming even when it is not watched,
        # otherwise the position can never be valued.
        #
        # Guarded because the trade is already committed and the cash already
        # debited. Letting this raise would report a 500 (or a red "could not
        # be executed" chip in chat) for a fill that did happen, which is a
        # far worse outcome than an unpriced ticker: the next restart
        # re-registers it anyway, and the row simply reads "-" until then.
        try:
            ensure_tracked(symbol)
        except Exception:
            logger.exception(
                "Trade committed but %s could not be added to the market "
                "source; it stays unpriced until the next restart.",
                symbol,
            )

    trade_row = row_to_dict(row)
    trade = {
        "id": trade_row["id"],
        "ticker": trade_row["ticker"],
        "side": trade_row["side"],
        "quantity": float(trade_row["quantity"]),
        "price": float(trade_row["price"]),
        "total": float(trade_row["quantity"]) * float(trade_row["price"]),
        "executed_at": trade_row["executed_at"],
    }
    return TradeResult(trade=trade, portfolio=build_portfolio(price_cache, user_id))


def _check_funding(
    side: Literal["buy", "sell"],
    ticker: str,
    quantity: float,
    total: float,
    *,
    user_id: str,
) -> None:
    """Pre-check cash / shares purely to produce a good error message.

    The authoritative check happens inside ``execute_trade_atomic``'s
    transaction; this one cannot be trusted under concurrency.
    """
    from .. import db

    if side == "buy":
        cash = float(db.get_cash_balance(user_id))
        if total > cash:
            raise TradeError(
                "INSUFFICIENT_CASH",
                f"Need ${total:,.2f} but only ${cash:,.2f} available.",
            )
        return

    position = db.get_position(ticker, user_id)
    held = float(row_to_dict(position)["quantity"]) if position is not None else 0.0
    if quantity > held:
        raise TradeError(
            "INSUFFICIENT_SHARES",
            f"Cannot sell {quantity:g} {ticker}: only {held:g} held.",
        )


def _as_trade_error(exc: Exception) -> Exception:
    """Translate the db layer's funding failures into TradeError."""
    from .. import db

    if isinstance(exc, db.InsufficientCashError):
        return TradeError("INSUFFICIENT_CASH", str(exc))
    if isinstance(exc, db.InsufficientSharesError):
        return TradeError("INSUFFICIENT_SHARES", str(exc))
    return exc
