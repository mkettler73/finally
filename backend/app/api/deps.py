"""Shared request dependencies.

The price cache and the session-open tracker are created once in the app
lifespan and live on ``app.state``; pulling them off the request keeps the
routers free of module-level singletons and testable with any app instance.
"""

from __future__ import annotations

from fastapi import Request

from ..market import PriceCache
from ..services.session_open import SessionOpenTracker


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_session_opens(request: Request) -> SessionOpenTracker:
    return request.app.state.session_opens
