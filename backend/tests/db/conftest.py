"""Fixtures for the data-layer tests.

Every test gets its own temp-file database — deliberately a file, not
`:memory:`, because an in-memory database is per-connection and would hide the
per-thread connection model these tests exist to exercise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import reset_db_for_tests
from app.db.connection import close_connection


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the data layer at a fresh temp database for the duration of a test."""
    path = tmp_path / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    reset_db_for_tests()
    yield path
    close_connection()


@pytest.fixture
def fresh_db(db_path: Path) -> Path:
    """Alias for tests that only care that a seeded database exists."""
    return db_path
