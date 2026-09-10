"""Business logic shared by the REST API and the LLM chat executor.

Nothing here imports FastAPI: every failure is raised as a plain exception
(``TradeError`` and the ``app.db`` exceptions) so the same code paths work
inside a request, inside the chat auto-executor and inside a background task.
"""

from __future__ import annotations

from .portfolio import STARTING_CASH, build_portfolio
from .session_open import SessionOpenTracker
from .snapshots import SNAPSHOT_INTERVAL_SECONDS, run_snapshot_loop
from .trading import TradeError, TradeResult, execute_trade

__all__ = [
    "STARTING_CASH",
    "SNAPSHOT_INTERVAL_SECONDS",
    "SessionOpenTracker",
    "TradeError",
    "TradeResult",
    "build_portfolio",
    "execute_trade",
    "run_snapshot_loop",
]
