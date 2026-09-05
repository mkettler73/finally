"""FastAPI application entrypoint for the FinAlly backend.

Wires the market data subsystem (see app/market/) into a running API:
starts the configured data source (simulator or Massive) on startup,
exposes the SSE price stream, and stops the source cleanly on shutdown.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .market import PriceCache, create_market_data_source, create_stream_router
from .market.seed_prices import SEED_PRICES

logger = logging.getLogger(__name__)

DEFAULT_TICKERS: list[str] = list(SEED_PRICES.keys())

# Shared across the app lifetime: the market data source (started/stopped by
# the lifespan below) writes into this cache, and the SSE router reads from it.
price_cache = PriceCache()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    market_source = create_market_data_source(price_cache)
    await market_source.start(DEFAULT_TICKERS)

    app.state.price_cache = price_cache
    app.state.market_source = market_source

    try:
        yield
    finally:
        await market_source.stop()


app = FastAPI(title="FinAlly API", lifespan=lifespan)

app.include_router(create_stream_router(price_cache))


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Health check for Docker/deployment."""
    return {"status": "ok"}
