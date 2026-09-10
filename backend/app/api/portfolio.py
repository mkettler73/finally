"""Portfolio endpoints: current state, trade execution, value history."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..market import PriceCache
from ..services.portfolio import build_portfolio
from ..services.rows import row_to_dict
from ..services.trading import execute_trade
from .deps import get_price_cache
from .errors import translate_db_errors

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

PriceCacheDep = Annotated[PriceCache, Depends(get_price_cache)]

# API_CONTRACT §3: default 500, max 5000.
DEFAULT_HISTORY_LIMIT = 500
MAX_HISTORY_LIMIT = 5000


class TradeRequest(BaseModel):
    """A market order. Deliberately permissive: `side` and `quantity` are
    validated in the trade service so manual and LLM trades produce identical
    error codes rather than one going through Pydantic and the other not."""

    ticker: str
    quantity: float
    side: str = Field(description="'buy' or 'sell'")


@router.get("")
def get_portfolio(price_cache: PriceCacheDep) -> dict[str, Any]:
    """Cash, positions valued at live prices, and totals."""
    with translate_db_errors():
        return build_portfolio(price_cache)


@router.post("/trade")
def post_trade(payload: TradeRequest, price_cache: PriceCacheDep) -> dict[str, Any]:
    """Execute a market order and return the trade plus the resulting portfolio."""
    result = execute_trade(
        payload.ticker,
        payload.side,  # type: ignore[arg-type]
        payload.quantity,
        price_cache=price_cache,
    )
    return result.to_response()


@router.get("/history")
def get_history(
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any]:
    """The most recent `limit` portfolio snapshots, oldest first."""
    from .. import db

    with translate_db_errors():
        snapshots = db.list_snapshots(limit)

    return {
        "snapshots": [
            {
                "total_value": float(row["total_value"]),
                "recorded_at": row["recorded_at"],
            }
            for row in (row_to_dict(snapshot) for snapshot in snapshots)
        ]
    }
