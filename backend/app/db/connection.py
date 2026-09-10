"""SQLite connection management, PRAGMA setup and lazy initialisation.

This is the only module in the codebase that opens a SQLite connection. FastAPI
runs sync endpoints in a threadpool while a background task writes portfolio
snapshots, so connections are **per thread** (`threading.local`) rather than one
shared handle with `check_same_thread=False`.

Connections run in autocommit mode (`isolation_level=None`); every multi-statement
write goes through `transaction()`, which issues an explicit `BEGIN IMMEDIATE` so
concurrent writers serialise on SQLite's write lock instead of failing to upgrade
a read transaction.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# backend/app/db/connection.py -> backend/app/db -> backend/app -> backend -> repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "finally.db"

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

REQUIRED_TABLES = (
    "users_profile",
    "watchlist",
    "positions",
    "trades",
    "portfolio_snapshots",
    "chat_messages",
)

_local = threading.local()

# Guards lazy init and the registry of live connections.
_lock = threading.Lock()
_initialised_paths: set[Path] = set()
_open_connections: list[sqlite3.Connection] = []
# Bumped by reset_db_for_tests so threads holding a handle it closed rebuild theirs.
_generation = 0


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a `Z` suffix.

    The single timestamp source for the whole data layer, so every stored
    timestamp has an identical shape (`2026-09-09T14:32:05.123456Z`).
    """
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def get_db_path() -> Path:
    """Resolved database path.

    `FINALLY_DB_PATH` wins; otherwise `db/finally.db` relative to the repo root
    (which is `/app/db/finally.db` inside the container, the volume mount).
    Read on every call so tests can repoint it.
    """
    raw = os.environ.get("FINALLY_DB_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DB_PATH


def _connect(path: Path) -> sqlite3.Connection:
    """Open a connection with the PRAGMAs required by DATA_LAYER.md §2."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def init_db() -> None:
    """Create the schema and seed defaults if they are missing. Idempotent.

    Safe to call from the app lifespan, from every connection handout, and
    concurrently from several threads at once (the whole check-and-create runs
    under a process-wide lock, and the DDL itself is `IF NOT EXISTS`).
    """
    path = get_db_path()
    with _lock:
        if path in _initialised_paths:
            return
        conn = _connect(path)
        try:
            if not set(REQUIRED_TABLES) <= _tables(conn):
                conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            # Imported here: seed uses the connection helpers in this module.
            from .seed import seed_defaults

            seed_defaults(conn)
        finally:
            conn.close()
        _initialised_paths.add(path)


def get_connection() -> sqlite3.Connection:
    """The calling thread's connection to the current database, initialised.

    A thread that outlives a change of `FINALLY_DB_PATH` (only tests do this)
    gets its stale handle closed and replaced.
    """
    path = get_db_path()
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None and (
        getattr(_local, "path", None) != path or getattr(_local, "generation", -1) != _generation
    ):
        _forget(conn)
        conn = None

    init_db()

    if conn is None:
        conn = _connect(path)
        _local.conn = conn
        _local.path = path
        _local.generation = _generation
        with _lock:
            _open_connections.append(conn)
    return conn


def _forget(conn: sqlite3.Connection) -> None:
    """Close a connection and drop it from the registry."""
    try:
        conn.close()
    except sqlite3.Error:  # pragma: no cover - closing a broken handle
        pass
    _local.conn = None
    _local.path = None
    with _lock:
        if conn in _open_connections:
            _open_connections.remove(conn)


@contextmanager
def read_transaction() -> Iterator[sqlite3.Connection]:
    """Run a block inside one `BEGIN DEFERRED` read snapshot.

    Several reads that must agree with each other need to see the same
    database state. Without this, valuing the portfolio reads the cash balance
    and the positions as two independent autocommit statements, and a trade
    committing in another thread between them yields cash-after with
    positions-before -- wrong by the whole trade notional. The 30s snapshot
    task then persists that figure, so a transient race becomes a permanent
    spike in the P&L chart.

    `BEGIN DEFERRED` rather than `IMMEDIATE`: under WAL a deferred reader gets
    a stable snapshot without taking the write lock, so trades are never
    blocked by portfolio valuation.
    """
    conn = get_connection()
    if conn.in_transaction:
        # Already inside one; nesting would commit the outer scope early.
        yield conn
        return

    conn.execute("BEGIN DEFERRED")
    try:
        yield conn
    finally:
        # Read-only: nothing to commit, and rolling back releases the snapshot
        # either way.
        if conn.in_transaction:
            conn.execute("ROLLBACK")


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block inside one `BEGIN IMMEDIATE` transaction.

    `BEGIN IMMEDIATE` takes the write lock up front, so a second writer waits out
    `busy_timeout` rather than discovering the conflict at COMMIT. Any exception
    rolls back — that is what makes a failed trade leave no trace.
    """
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def close_connection() -> None:
    """Close this thread's connection, if it has one."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        _forget(conn)


def reset_db_for_tests() -> None:
    """Drop every connection, delete the database file, and re-initialise.

    Test fixtures only. Point `FINALLY_DB_PATH` at a temp file first — this
    deletes whatever it is aimed at.
    """
    global _generation
    with _lock:
        for conn in _open_connections:
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - closing a broken handle
                pass
        _open_connections.clear()
        _initialised_paths.clear()
        _generation += 1
    _local.conn = None
    _local.path = None

    path = get_db_path()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    init_db()
