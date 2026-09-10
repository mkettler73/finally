"""HTTP layer: routers and the shared error envelope."""

from __future__ import annotations

from .errors import ApiError, register_exception_handlers
from .portfolio import router as portfolio_router
from .watchlist import router as watchlist_router

__all__ = [
    "ApiError",
    "portfolio_router",
    "register_exception_handlers",
    "watchlist_router",
]
