"""Late-bound access to the sibling backend modules the chat layer depends on.

`app.db` and `app.services.trading` are owned by other agents and are resolved
through `importlib` at call time rather than imported at module scope. That
buys two things:

1. `app.llm` imports cleanly (and its unit tests collect) before those modules
   land, which is what lets the chat layer be built in parallel against the
   frozen contracts in DATA_LAYER.md and API_CONTRACT.md §7.
2. Tests can install fakes in `sys.modules` and have every call site pick them
   up, without any module-level patching gymnastics.
"""

from __future__ import annotations

import importlib
from types import ModuleType

DB_MODULE = "app.db"
TRADING_MODULE = "app.services.trading"


def get_db() -> ModuleType:
    """The data layer surface described in DATA_LAYER.md §4."""
    return importlib.import_module(DB_MODULE)


def get_trading() -> ModuleType:
    """The shared trade service frozen in API_CONTRACT.md §7."""
    return importlib.import_module(TRADING_MODULE)
