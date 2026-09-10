"""Watchlist endpoints. Adding or removing also starts or stops streaming."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..market import PriceCache
from ..services.market_registry import ensure_tracked, untrack
from ..services.portfolio import DUST_QUANTITY
from ..services.rows import row_to_dict
from ..services.session_open import SessionOpenTracker
from ..services.trading import normalize_ticker
from ..services.watchlist import build_watchlist_row
from .deps import get_price_cache, get_session_opens
from .errors import translate_db_errors

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

PriceCacheDep = Annotated[PriceCache, Depends(get_price_cache)]
SessionOpensDep = Annotated[SessionOpenTracker, Depends(get_session_opens)]


class WatchlistAddRequest(BaseModel):
    ticker: str


@router.get("")
def get_watchlist(
    price_cache: PriceCacheDep,
    session_opens: SessionOpensDep,
) -> dict[str, Any]:
    """Watched tickers with live prices, oldest first."""
    from .. import db

    # Catches the session open of anything added since the last read.
    session_opens.observe_cache(price_cache)

    with translate_db_errors():
        entries = db.list_watchlist()

    return {
        "tickers": [build_watchlist_row(entry, price_cache, session_opens) for entry in entries]
    }


@router.post("", status_code=201)
def add_watchlist_ticker(
    payload: WatchlistAddRequest,
    price_cache: PriceCacheDep,
    session_opens: SessionOpensDep,
) -> dict[str, Any]:
    """Add a ticker to the watchlist and start streaming it."""
    from .. import db

    ticker = normalize_ticker(payload.ticker)

    with translate_db_errors():
        entry = db.add_to_watchlist(ticker)

    ensure_tracked(ticker)
    session_opens.observe(ticker, price_cache.get_price(ticker))

    return {"ticker": build_watchlist_row(entry, price_cache, session_opens)}


@router.delete("/{ticker}")
def remove_watchlist_ticker(
    ticker: str,
    session_opens: SessionOpensDep,
) -> dict[str, Any]:
    """Remove a ticker, keeping it streaming if a position still holds it."""
    from .. import db

    symbol = normalize_ticker(ticker)

    with translate_db_errors():
        position = db.get_position(symbol)
        db.remove_from_watchlist(symbol)

    quantity = float(row_to_dict(position)["quantity"]) if position is not None else 0.0
    if quantity <= DUST_QUANTITY:
        # Nothing holds it, so stop paying to price it. A held ticker must keep
        # streaming or the portfolio cannot be valued.
        untrack(symbol)
        session_opens.forget(symbol)

    return {"ticker": symbol, "removed": True}
