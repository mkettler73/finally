"""Row normalisation.

``planning/DATA_LAYER.md`` specifies the repository's return types as
"TypedDicts or dataclasses" without settling on one. A TypedDict is a dict at
runtime and a dataclass is not, so consumers cannot index both. Every db row
crossing into this package goes through ``row_to_dict`` first, which makes the
service layer indifferent to that choice.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any


def row_to_dict(row: Any) -> dict[str, Any]:
    """Return a plain dict view of a repository row."""
    if isinstance(row, Mapping):
        return dict(row)
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        return dataclasses.asdict(row)
    if hasattr(row, "_asdict"):  # namedtuple
        return dict(row._asdict())
    if hasattr(row, "keys"):  # sqlite3.Row
        return {key: row[key] for key in row.keys()}
    return dict(vars(row))
