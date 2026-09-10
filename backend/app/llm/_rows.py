"""One accessor for rows and result objects whose concrete type is not pinned.

DATA_LAYER.md section 4 says the repository returns "TypedDicts or dataclasses",
and API_CONTRACT.md section 7 describes `TradeResult.trade` as "the trade object
from section 3" without saying which. Rather than bet on one and break on the
other - or block on an answer - the chat layer reads fields through here, which
handles mappings, `sqlite3.Row`, and plain objects alike.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off a mapping, a subscriptable row, or an object."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    try:
        return obj[name]
    except (TypeError, KeyError, IndexError):
        pass
    return getattr(obj, name, default)
