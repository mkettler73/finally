"""Access to the running market data source from synchronous code.

The trade service and the watchlist router have to call
``MarketDataSource.add_ticker`` / ``remove_ticker``, which are coroutines, from
synchronous functions that FastAPI runs in its threadpool. The registry holds
the source together with the event loop it belongs to, so those calls can be
bridged with ``run_coroutine_threadsafe`` instead of being fired at whatever
loop happens to be around.

The lifespan in ``app.main`` populates it; tests set a fake source directly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from threading import Lock
from typing import Any

from ..market import MarketDataSource

logger = logging.getLogger(__name__)

# How long a synchronous caller waits for add/remove_ticker to land on the loop.
_BRIDGE_TIMEOUT_SECONDS = 5.0

_lock = Lock()
_source: MarketDataSource | None = None
_loop: asyncio.AbstractEventLoop | None = None


def set_market_source(
    source: MarketDataSource | None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Register the live source and the loop it runs on."""
    global _source, _loop
    with _lock:
        _source = source
        _loop = loop


def get_market_source() -> MarketDataSource | None:
    """The live source, or None when the app has not started one."""
    with _lock:
        return _source


def clear_market_source() -> None:
    set_market_source(None, None)


def _run(coro: Coroutine[Any, Any, None]) -> None:
    """Run a source coroutine to completion from synchronous code.

    Must be called from a worker thread, never from the event loop itself.
    """
    with _lock:
        loop = _loop

    if loop is not None and not loop.is_closed():
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None

        if current is loop:
            # Scheduling onto the loop and then blocking on the result from
            # that same loop deadlocks: the thread that would run the
            # coroutine is the thread waiting for it, so nothing progresses
            # until the timeout fires. Callers on the loop must hand the whole
            # synchronous operation to a worker thread (asyncio.to_thread)
            # rather than reaching here. Schedule and return instead of
            # blocking, so a mistake degrades to "the ticker starts streaming
            # a moment later" rather than freezing the app.
            logger.error(
                "market_registry._run called from the event loop thread; "
                "scheduling without waiting. The caller should use "
                "asyncio.to_thread - see app/llm/executor.py.",
            )
            loop.create_task(coro)
            return

        asyncio.run_coroutine_threadsafe(coro, loop).result(_BRIDGE_TIMEOUT_SECONDS)
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
    else:
        # Called from inside a loop with no registered loop (only happens in
        # tests): schedule it and let it complete on its own.
        running.create_task(coro)


def ensure_tracked(ticker: str) -> None:
    """Add a ticker to the live source unless it is already streaming.

    A position must stay priced even when its ticker is not on the watchlist,
    so every buy funnels through here.
    """
    source = get_market_source()
    if source is None:
        logger.debug("No market source registered; not tracking %s", ticker)
        return
    if ticker in source.get_tickers():
        return
    _run(source.add_ticker(ticker))


def untrack(ticker: str) -> None:
    """Remove a ticker from the live source."""
    source = get_market_source()
    if source is None:
        return
    _run(source.remove_ticker(ticker))
