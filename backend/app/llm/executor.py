"""Executes the actions the model asked for, and reports honestly on each one.

Two rules shape this module:

* Trades run first, then watchlist changes, each in the order the model listed
  them (API_CONTRACT.md section 6).
* A refused action is not a failed request. Every trade goes through the one
  shared `app.services.trading.execute_trade`, and when that raises, the
  failure becomes an `{"status": "error"}` action on an otherwise normal 200
  response - the model gets told, the user sees a red chip, nothing 500s.

Database and trade calls are made directly on the event loop rather than
handed to a worker thread: they are local SQLite writes measured in
microseconds, and keeping them on the loop avoids any assumption about whether
the trade service is safe to call off it. The one genuinely slow call, the
model request itself, is threaded by the router.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..market import MarketDataSource, PriceCache
from ._deps import get_db, get_trading
from ._rows import field as _field
from .mock import format_quantity
from .models import ActionResult, ChatResponse, ChatTrade, ChatWatchlistChange

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


def _normalize(ticker: str) -> str:
    return MarketDataSource.normalize_ticker(ticker)


def _valid_ticker(ticker: str) -> bool:
    return bool(TICKER_RE.match(ticker))


def _message_of(exc: Exception, fallback: str) -> str:
    """Prefer the exception's own human-readable message; never surface an empty string."""
    message = getattr(exc, "message", None) or str(exc)
    return message.strip() or fallback


async def execute_actions(
    response: ChatResponse,
    *,
    price_cache: PriceCache,
    market_source: MarketDataSource | None = None,
    session_opens: Any = None,
    user_id: str = "default",
) -> list[ActionResult]:
    """Apply every trade, then every watchlist change, returning one result each.

    `session_opens` is the API layer's `SessionOpenTracker` (duck-typed here to
    keep `app.llm` from depending on `app.services`). It is updated exactly as
    the watchlist router updates it, so a ticker added through chat gets the
    same day-change baseline as one added through the button.
    """
    results: list[ActionResult] = []

    for trade in response.trades:
        # Off the event loop, deliberately. `execute_trade_action` is fully
        # synchronous: it does blocking SQLite I/O, and for a ticker that is
        # not yet streaming it calls `ensure_tracked`, which bridges back onto
        # this very loop with `run_coroutine_threadsafe(...).result()`. Run
        # from the loop thread that self-deadlocks -- the coroutine cannot be
        # serviced by a thread that is blocked waiting for it -- and the whole
        # app, SSE included, freezes until the 5s bridge timeout fires.
        # Handing it to a worker thread is also what keeps a chat trade from
        # stalling every other client's price stream.
        results.append(
            await asyncio.to_thread(
                execute_trade_action, trade, price_cache=price_cache, user_id=user_id
            )
        )

    for change in response.watchlist_changes:
        results.append(
            await apply_watchlist_action(
                change,
                price_cache=price_cache,
                market_source=market_source,
                session_opens=session_opens,
                user_id=user_id,
            )
        )

    return results


def execute_trade_action(
    trade: ChatTrade,
    *,
    price_cache: PriceCache,
    user_id: str = "default",
) -> ActionResult:
    """Run one model-requested trade through the shared trade service."""
    ticker = _normalize(trade.ticker)
    requested = {"ticker": ticker, "side": trade.side, "quantity": trade.quantity}

    if not _valid_ticker(ticker):
        return ActionResult(
            type="trade",
            status="error",
            detail=f"'{trade.ticker}' is not a valid ticker symbol.",
            data=requested,
        )

    trading = get_trading()
    try:
        result = trading.execute_trade(
            ticker,
            trade.side,
            trade.quantity,
            price_cache=price_cache,
            user_id=user_id,
        )
    except trading.TradeError as exc:
        return ActionResult(
            type="trade",
            status="error",
            detail=_message_of(exc, "The trade was rejected."),
            data=requested,
        )
    except Exception as exc:  # noqa: BLE001 - one bad trade must not sink the chat turn
        logger.exception("Unexpected failure executing chat trade %s", requested)
        return ActionResult(
            type="trade",
            status="error",
            detail=f"The trade could not be executed: {exc}",
            data=requested,
        )

    executed = _field(result, "trade", {})
    quantity = float(_field(executed, "quantity", trade.quantity) or 0.0)
    price = float(_field(executed, "price", 0.0) or 0.0)
    total = _field(executed, "total")
    if total is None:
        total = quantity * price
    verb = "Bought" if trade.side == "buy" else "Sold"

    return ActionResult(
        type="trade",
        status="ok",
        detail=f"{verb} {format_quantity(quantity)} {ticker} @ ${price:,.2f}",
        data={
            "ticker": ticker,
            "side": trade.side,
            "quantity": quantity,
            "price": price,
            "total": float(total),
        },
    )


async def apply_watchlist_action(
    change: ChatWatchlistChange,
    *,
    price_cache: PriceCache | None = None,
    market_source: MarketDataSource | None = None,
    session_opens: Any = None,
    user_id: str = "default",
) -> ActionResult:
    """Apply one model-requested watchlist mutation.

    Mirrors `POST`/`DELETE /api/watchlist` (API_CONTRACT.md section 4): an add
    also registers the ticker with the live data source so it streams
    immediately, and a remove only unregisters it when no open position still
    needs it priced.
    """
    ticker = _normalize(change.ticker)
    requested = {"ticker": ticker, "action": change.action}

    if not _valid_ticker(ticker):
        return ActionResult(
            type="watchlist",
            status="error",
            detail=f"'{change.ticker}' is not a valid ticker symbol.",
            data=requested,
        )

    db = get_db()

    if change.action == "add":
        try:
            db.add_to_watchlist(ticker, user_id)
        except (db.DuplicateTickerError, db.WatchlistFullError) as exc:
            return ActionResult(
                type="watchlist",
                status="error",
                detail=_message_of(exc, f"Could not add {ticker} to the watchlist."),
                data=requested,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected failure adding %s to the watchlist", ticker)
            return ActionResult(
                type="watchlist",
                status="error",
                detail=f"Could not add {ticker} to the watchlist: {exc}",
                data=requested,
            )

        await _track(market_source, ticker)
        if session_opens is not None and price_cache is not None:
            session_opens.observe(ticker, price_cache.get_price(ticker))
        return ActionResult(
            type="watchlist",
            status="ok",
            detail=f"Added {ticker} to the watchlist",
            data=requested,
        )

    try:
        db.remove_from_watchlist(ticker, user_id)
    except db.TickerNotFoundError as exc:
        return ActionResult(
            type="watchlist",
            status="error",
            detail=_message_of(exc, f"{ticker} is not on the watchlist."),
            data=requested,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected failure removing %s from the watchlist", ticker)
        return ActionResult(
            type="watchlist",
            status="error",
            detail=f"Could not remove {ticker} from the watchlist: {exc}",
            data=requested,
        )

    # A held position must keep streaming so the portfolio can still be valued.
    if db.get_position(ticker, user_id) is None:
        await _untrack(market_source, ticker)
        if session_opens is not None:
            session_opens.forget(ticker)

    return ActionResult(
        type="watchlist",
        status="ok",
        detail=f"Removed {ticker} from the watchlist",
        data=requested,
    )


async def _track(source: MarketDataSource | None, ticker: str) -> None:
    if source is None:
        logger.warning("No market data source available; %s will not stream yet", ticker)
        return
    try:
        await source.add_ticker(ticker)
    except Exception:  # noqa: BLE001 - the watchlist row is already committed
        logger.exception("Failed to register %s with the market data source", ticker)


async def _untrack(source: MarketDataSource | None, ticker: str) -> None:
    if source is None:
        return
    try:
        await source.remove_ticker(ticker)
    except Exception:  # noqa: BLE001 - the watchlist row is already removed
        logger.exception("Failed to unregister %s from the market data source", ticker)


__all__ = [
    "apply_watchlist_action",
    "execute_actions",
    "execute_trade_action",
]
