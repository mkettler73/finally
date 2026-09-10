"""FastAPI application entrypoint for the FinAlly backend.

The lifespan owns every piece of process-wide state: the price cache, the
market data source (simulator or Massive), the session-open tracker that backs
the watchlist's day-change column, and the periodic portfolio snapshot task.
Routers are registered before the static export is mounted, so `/api/*` always
takes precedence over the frontend's catch-all.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI

from .api import portfolio_router, register_exception_handlers, watchlist_router
from .api.static import mount_static
from .market import PriceCache, create_market_data_source, create_stream_router
from .market.seed_prices import SEED_PRICES
from .services.market_registry import clear_market_source, set_market_source
from .services.session_open import SessionOpenTracker
from .services.snapshots import SNAPSHOT_INTERVAL_SECONDS, run_snapshot_loop

logger = logging.getLogger(__name__)

# PLAN.md §5: the backend reads .env from the project root. In Docker the
# variables arrive via --env-file and this finds nothing, which is correct --
# `override=False` means a real environment variable always wins. It matters
# for the documented local workflow (`uv run uvicorn app.main:app`), which
# otherwise starts with no OPENROUTER_API_KEY and answers every chat request
# with a 502.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

DEFAULT_TICKERS: list[str] = list(SEED_PRICES.keys())

# Shared across the app lifetime: the market data source (started/stopped by
# the lifespan below) writes into this cache, and the SSE router reads from it.
price_cache = PriceCache()

# First price seen per ticker since process start; backs session_change_percent.
session_opens = SessionOpenTracker()


def _load_chat_router() -> APIRouter | None:
    """Build the LLM chat router.

    `app.llm` exposes a factory rather than a module-level router, for the same
    reason the SSE router does: a router declared at module scope binds to the
    first price cache it ever sees. Everything else the router needs
    (market source, session-open tracker, client) it resolves per request from
    `app.state`, so the price cache is the only argument available this early.

    The import is deliberately guarded so a missing or broken `app.llm` costs
    the chat endpoint rather than the whole application - but it logs at ERROR,
    not silently, because a booting app with no `/api/chat` is a bug and must
    look like one in the logs.
    """
    try:
        from .llm import create_chat_router
    except ImportError:
        logger.exception("Could not import app.llm - /api/chat is unavailable.")
        return None

    return create_chat_router(price_cache)


def _startup_tickers(db: Any) -> list[str]:
    """Every ticker the app must price: the defaults, plus persisted state.

    The database outlives the process (it is on a Docker volume), so starting
    from DEFAULT_TICKERS alone strands anything the user added or bought in an
    earlier run. A held position that is not streaming is the worse half of
    that: it is valued at its average cost, its row reads "-" forever, and
    `execute_trade` rejects every sell with PRICE_UNAVAILABLE, so the holding
    cannot be liquidated at all.

    Order matters -- the defaults come first so the watchlist keeps its seeded
    order -- and it must be deduplicated, since the two sources overlap.
    """
    tickers: list[str] = list(DEFAULT_TICKERS)
    seen = set(tickers)

    for source_name, rows in (
        ("watchlist", db.list_watchlist()),
        ("positions", db.list_positions()),
    ):
        for row in rows:
            ticker = str(row["ticker"]).strip().upper()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
                logger.info("Restoring %s from persisted %s", ticker, source_name)

    return tickers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from . import db

    # Lazy: creates and seeds db/finally.db on a fresh volume.
    db.init_db()

    market_source = create_market_data_source(price_cache)
    await market_source.start(_startup_tickers(db))

    # start() seeds the cache, so every default ticker gets a session open
    # immediately rather than on the first watchlist read.
    session_opens.observe_cache(price_cache)
    set_market_source(market_source, asyncio.get_running_loop())

    app.state.price_cache = price_cache
    app.state.market_source = market_source
    app.state.session_opens = session_opens

    snapshot_task = asyncio.create_task(
        run_snapshot_loop(
            price_cache,
            interval=SNAPSHOT_INTERVAL_SECONDS,
            session_opens=session_opens,
        ),
        name="portfolio-snapshots",
    )
    app.state.snapshot_task = snapshot_task

    try:
        yield
    finally:
        snapshot_task.cancel()
        try:
            await snapshot_task
        except asyncio.CancelledError:
            pass
        clear_market_source()
        await market_source.stop()


app = FastAPI(title="FinAlly API", lifespan=lifespan)

register_exception_handlers(app)

app.include_router(create_stream_router(price_cache))
app.include_router(portfolio_router)
app.include_router(watchlist_router)

_chat_router = _load_chat_router()
if _chat_router is not None:
    app.include_router(_chat_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Health check for Docker/deployment."""
    return {"status": "ok"}


# Must be last: a mount at "/" matches everything that no route above claimed.
mount_static(app)
